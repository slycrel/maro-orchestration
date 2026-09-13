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


def _related(n, reason="follows it", continues=True):
    return json.dumps({"relation": "related", "run": n, "continues": continues, "reason": reason})


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
        if purpose == "landscape" and isinstance(text, list):
            # scripted in order; the last answer repeats
            text = text.pop(0) if len(text) > 1 else text[0]
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


# ---------------------------------------------------------------------------
# review round 2026-09-05 (Skeptic + Expert QA, codex)
# ---------------------------------------------------------------------------

class TestReviewFixes:
    def test_only_lifecycle_terminal_statuses_are_candidates(self, monkeypatch, tmp_path):
        # a failed prior IS landscape information (its outcome is shown);
        # a run still running, or a status the lifecycle never writes, is not
        _setup(monkeypatch, tmp_path)
        from landscape import candidates, prompt
        err = _finished_run(GOAL_QUARTERLY, status="error", handle_id="aaaa0001")
        killed = _finished_run(GOAL_QUARTERLY, status="killed", handle_id="aaaa0002")
        done = _finished_run(GOAL_QUARTERLY, status="done", handle_id="aaaa0003")
        _finished_run(GOAL_QUARTERLY, status="running", handle_id="aaaa0004")     # stale ended_at
        _finished_run(GOAL_QUARTERLY, status="Whatever", handle_id="aaaa0005")   # not a lifecycle word
        _finished_run(GOAL_QUARTERLY, status="", handle_id="aaaa0006")
        cands, scanned, below = candidates(GOAL_QUARTERLY)
        assert scanned == 3 and below == 0
        assert [c["handle_id"] for c in cands] == [done, killed, err]
        assert [c["status"] for c in cands] == ["done", "killed", "error"]
        assert f"(run {err}, similarity 1.00, outcome error)" in prompt(GOAL_QUARTERLY, cands)

    def test_the_cap_counts_eligible_runs_not_directories(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import landscape
        from landscape import candidates, decide
        import os, time
        old = _finished_run(GOAL_QUARTERLY, "the old answer", handle_id="aaaa0001")
        rd = tmp_path / "runs"
        t0 = time.time() - 3600
        os.utime(next(rd.glob("aaaa0001-*")), (t0, t0))
        # newer junk: unfinished, dry, and metadata-less directories, more than the cap
        for i in range(landscape.SCAN_CAP + 5):
            if i % 3 == 0:
                _finished_run(GOAL_HAIKU, finished=False)
            elif i % 3 == 1:
                _finished_run(GOAL_HAIKU, dry_run=True)
            else:
                (rd / f"junk{i:04d}-dir").mkdir()
        cands, scanned, below = candidates(GOAL_QUARTERLY)
        assert [c["handle_id"] for c in cands] == [old] and scanned == 1
        rec = decide(GOAL_QUARTERLY, handle_id="x", adapter=_Judge(_related(1)))
        assert rec["chosen"] == old and "truncated" not in rec
        # more ELIGIBLE runs than the cap: the newest are read, the record says it stopped
        monkeypatch.setattr(landscape, "SCAN_CAP", 2)
        _finished_run(GOAL_HAIKU, handle_id="bbbb0001")
        _finished_run(GOAL_HAIKU, handle_id="bbbb0002")
        rec = decide(GOAL_QUARTERLY, handle_id="x", adapter=_Judge())
        assert rec["rule"] == "no_candidates" and rec["scanned"] == 2 and rec["truncated"] is True

    def test_the_third_contract_reads_strictly(self):
        from landscape import parse, PROMPT_VER
        cands = TestParse.CANDS
        assert PROMPT_VER == 4
        # the second contract, as recorded, still reads as it did
        assert parse('{"relation":"related","run":1.9,"reason":"x"}', cands, ver=2)[1] == "aaaa0001"
        assert parse('{"relation":"fresh","run":2,"reason":"x"}', cands, ver=2)[0] == "fresh"
        for bad in ('{"relation":"related","run":1.9,"reason":"x"}',
                    '{"relation":"related","run":"1.9","reason":"x"}',
                    '{"relation":"fresh","run":2,"reason":"x"}',
                    '{"relation":"fresh","run":"aaaa0001","reason":"x"}',
                    '{"relation":"fresh","run":[1],"reason":"x"}'):
            with pytest.raises(ValueError):
                parse(bad, cands)
        assert parse('{"relation":"related","run":2.0,"reason":"x"}', cands)[1] == "aaaa0002"
        for ok in ('{"relation":"fresh","run":0,"reason":"x"}', '{"relation":"fresh","run":"0","reason":"x"}',
                   '{"relation":"fresh","reason":"x"}', '{"relation":"fresh","run":null,"reason":"x"}'):
            assert parse(ok, cands) == ("fresh", "", "x")

    def test_a_chosen_run_outside_the_candidates_is_no_decision(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from landscape import apply
        from recall import lineage_root
        b = _finished_run(GOAL_FOLLOW_UP, finished=False)
        rec = {"rule": "judge", "relation": "related", "chosen": "zzzz9999",
               "candidates": [{"handle_id": "aaaa0001", "goal": GOAL_QUARTERLY}]}
        with pytest.raises(ValueError):
            apply(b, {"source": "cli"}, rec)
        assert lineage_root(b) == b
        from runs import run_dir
        assert "landscape" not in json.loads((run_dir(b) / "metadata.json").read_text())

    def test_an_unrecorded_decision_does_not_drive_the_run(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        from landscape import apply, decide
        a = _finished_run(GOAL_QUARTERLY, "Revenue rose.")
        b = _finished_run(GOAL_FOLLOW_UP, finished=False)
        rec = decide(GOAL_FOLLOW_UP, handle_id=b, adapter=_Judge(_related(1)))
        monkeypatch.setattr(runs, "stamp_run_metadata_for", lambda *x, **kw: None)
        with pytest.raises(RuntimeError):
            apply(b, None, rec)

    def test_a_failed_stage_runs_fresh_and_says_so(self, monkeypatch, tmp_path, caplog):
        _setup(monkeypatch, tmp_path)
        import landscape
        from handle import handle
        from runs import run_dir
        _finished_run(GOAL_QUARTERLY, "Revenue rose.")
        monkeypatch.setattr(landscape, "apply", lambda *x, **kw: (_ for _ in ()).throw(RuntimeError("disk full")))
        a = _NowAndJudge(_related(1))
        with _no_hosted_free(), _classify_now():
            r = handle(GOAL_QUARTERLY, adapter=a, force_lane="now", dry_run=False,
                       origin={"source": "cli"})
        assert r.status == "done"
        meta = json.loads((run_dir(r.handle_id) / "metadata.json").read_text())
        assert meta["landscape"] == {"rule": "judge_unreadable", "relation": "fresh",
                                     "reason": "stage failed: disk full"}
        assert "parent_handle_id" not in meta.get("origin", {})
        assert "## Related prior run" not in a.calls[-1][0][-1].content
        assert any("running fresh" in m for m in caplog.messages)

    def test_a_landscape_relation_writes_no_project_ancestry(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        from handle import handle, _default_project_for
        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        from orch_items import project_dir
        import llm
        a = _finished_run("Investigate the revenue ledger for the board", "Ledger reviewed.")
        adapter = _NowAndJudge(_related(1, "same ledger"))
        monkeypatch.setattr(llm, "build_adapter", lambda *x, **kw: adapter)
        goal = "Audit committee review of the revenue ledger"
        assert _default_project_for(goal) != _default_project_for("Investigate the revenue ledger for the board")

        def _fake_run(g, *x, **kw):
            return LoopResult(loop_id="l", project=kw.get("project") or "p", goal=g, status="done",
                              stuck_reason=None, steps=[StepOutcome(index=0, text="s", status="done",
                                                                    result="o", iteration=0)])
        gate = MagicMock(); gate.escalate = False; gate.contested_claims = []
        with _no_hosted_free(), patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   return_value=ClosureVerdict(complete=True, confidence=0.9, gaps=[],
                                               summary="v", checks_run=1, checks_passed=1)), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            r = handle(goal, force_lane="agenda", dry_run=False)
        from runs import run_dir
        meta = json.loads((run_dir(r.handle_id) / "metadata.json").read_text())
        assert meta["origin"]["parent_handle_id"] == a and meta["origin"]["related_by"] == "landscape"
        assert not list(tmp_path.rglob("ancestry.json"))


# ---------------------------------------------------------------------------
# project binding (2026-09-13): the landscape's decision binds the loop's
# project — decree [[feedback_decisions_belong_to_maro]], BACKLOG #65
# ---------------------------------------------------------------------------

def _agenda_run(monkeypatch, goal, adapter, **kw):
    """Run `goal` on the AGENDA lane with the loop stubbed; returns
    (HandleResult, the loop's kwargs)."""
    from unittest.mock import MagicMock
    from handle import handle
    from agent_loop import LoopResult, StepOutcome
    from director import ClosureVerdict
    import llm
    monkeypatch.setattr(llm, "build_adapter", lambda *x, **k: adapter)
    loop_kwargs = []

    def _fake_run(g, *x, **k):
        loop_kwargs.append(k)
        return LoopResult(loop_id="test-bind", project=k.get("project", ""), goal=g, status="done",
                          stuck_reason=None,
                          steps=[StepOutcome(index=0, text="step", status="done", result="output", iteration=0)])

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
        r = handle(goal, force_lane="agenda", dry_run=False, **kw)
    assert loop_kwargs, "the loop ran"
    return r, loop_kwargs[0]


def _meta(handle_id):
    from runs import run_dir
    return json.loads((run_dir(handle_id) / "metadata.json").read_text())


class TestTheLandscapeBindsTheProject:
    def test_a_related_goal_lands_in_the_chosen_runs_project(self, monkeypatch, tmp_path):
        # BACKLOG #65: the follow-up whose wording names no project must
        # land where the run it continues did its work — the landscape's
        # decision, not a string match, binds the project.
        _setup(monkeypatch, tmp_path)
        from handle import _default_project_for
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        prior = _finished_run(GOAL_QUARTERLY, "Revenue rose 12% on services.", extra={"project": "board-reports"})
        assert _default_project_for(GOAL_FOLLOW_UP) != "board-reports", "the fixture's wording mints another slug"
        r, kw = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "the same report, with margins")))
        assert kw["project"] == "board-reports"
        meta = _meta(r.handle_id)
        assert meta["landscape"]["chosen"] == prior and meta["origin"]["relation"] == "related"
        assert (meta["project"], meta["project_binding"]) == ("board-reports", "landscape")

    def test_a_rerun_binds_the_same_way(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        r, kw = _agenda_run(monkeypatch, GOAL_QUARTERLY, _NowAndJudge(_rerun(1, "same ask")))
        assert kw["project"] == "board-reports" and _meta(r.handle_id)["project_binding"] == "landscape"

    def test_the_operator_project_overrides_the_landscape(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        r, kw = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "follows it")),
                            project="investor-deck")
        assert kw["project"] == "investor-deck"
        meta = _meta(r.handle_id)
        assert meta["landscape"]["chosen"] and (meta["project"], meta["project_binding"]) == ("investor-deck", "operator")

    def test_fresh_goals_keep_the_goal_text_derivation_and_say_which_rule(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from handle import _default_project_for
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        # --fresh: no landscape decision → the minted slug, recorded as such
        r, kw = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(), fresh=True)
        assert kw["project"] == _default_project_for(GOAL_FOLLOW_UP)
        assert _meta(r.handle_id)["project_binding"] == "minted"
        # a fresh goal that literally names an existing project: the named shortcut, recorded as such
        r2, kw2 = _agenda_run(monkeypatch, "Refresh the board-reports index page", _NowAndJudge(), fresh=True)
        assert kw2["project"] == "board-reports" and _meta(r2.handle_id)["project_binding"] == "named"

    def test_a_chosen_run_whose_project_is_gone_falls_back(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from handle import _default_project_for
        import landscape
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})  # no dir
        r, kw = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "follows it")))
        assert kw["project"] == _default_project_for(GOAL_FOLLOW_UP)
        meta = _meta(r.handle_id)
        assert meta["landscape"]["chosen"] and meta["project_binding"] == "minted"
        # the unit: fresh, no chosen, no recorded project, a path-shaped project
        assert landscape.chosen_project({"relation": "fresh"}) == ""
        assert landscape.chosen_project({"relation": "related", "chosen": ""}) == ""
        a = _finished_run(GOAL_HAIKU, "leaves", extra={"project": "../escape"})
        assert landscape.chosen_project({"relation": "related", "chosen": a}) == ""
        b = _finished_run(GOAL_HAIKU, "leaves")
        assert landscape.chosen_project({"relation": "related", "chosen": b}) == ""


class TestTheJudgeSaysWhetherTheGoalContinuesTheWork:
    """Review round 1 (2026-09-13): `related` covers a tangent whose answer is
    useful context — a context relation must not be promoted into a workspace
    decision. The fourth template shows the judge each candidate's project
    and asks whether the goal CONTINUES the chosen run's work; only that
    binds the project."""

    def test_the_fourth_contract_shows_the_project_and_reads_continues_strictly(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from landscape import decide, parse, parse_full, prompt, PROMPT_VER
        assert PROMPT_VER == 4
        a = _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        judge = _Judge(_related(1, "carries it forward"))
        rec = decide(GOAL_FOLLOW_UP, handle_id="x", adapter=judge)
        text = judge.calls[0][0][-1].content
        assert "Project: board-reports" in text and '"continues":' in text
        assert rec["continues"] is True and rec["candidates"][0]["project"] == "board-reports"
        # the older template neither shows the project nor asks
        old = prompt(GOAL_FOLLOW_UP, rec["candidates"], ver=3)
        assert "Project:" not in old and "continues" not in old
        cands = rec["candidates"]
        # only the JSON boolean true continues; a string, a number, or silence does not
        assert parse_full('{"relation":"related","run":1,"continues":true,"reason":"x"}', cands)[3] is True
        for not_it in ('"true"', '1', 'null', '"yes"'):
            assert parse_full('{"relation":"related","run":1,"continues":%s,"reason":"x"}' % not_it, cands)[3] is False
        assert parse_full('{"relation":"related","run":1,"reason":"x"}', cands)[3] is False
        # a rerun always continues; fresh never; an older template never
        assert parse_full('{"relation":"rerun","run":1,"reason":"x"}', cands)[3] is True
        assert parse_full('{"relation":"fresh","continues":true,"reason":"x"}', cands)[3] is False
        assert parse_full('{"relation":"related","run":1,"continues":true,"reason":"x"}', cands, ver=3)[3] is False
        # `parse` is unchanged for its callers
        assert parse('{"relation":"related","run":1,"continues":true,"reason":"x"}', cands) == ("related", a, "x")
        # a judge that does not say the goal continues records that
        rec2 = decide(GOAL_FOLLOW_UP, handle_id="x", adapter=_Judge(_related(1, "same method", continues=False)))
        assert rec2["relation"] == "related" and rec2["chosen"] == a and rec2["continues"] is False

    def test_a_related_run_that_is_only_context_does_not_bind_the_project(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from handle import _default_project_for
        from orch_items import projects_root
        (projects_root() / "client-a").mkdir(parents=True)
        prior = _finished_run(GOAL_QUARTERLY, "Revenue rose 12% on services.", extra={"project": "client-a"})
        goal = "Use that approach for the client B quarterly revenue report"
        r, kw = _agenda_run(monkeypatch, goal,
                            _NowAndJudge(_related(1, "the same method for other work", continues=False)))
        meta = _meta(r.handle_id)
        # the relation and its context stand ...
        assert meta["landscape"]["chosen"] == prior and meta["origin"]["relation"] == "related"
        assert meta["landscape"]["continues"] is False
        # ... but the deliverable does not land in client A's project
        assert kw["project"] == _default_project_for(goal) != "client-a"
        assert (meta["project"], meta["project_binding"]) == (kw["project"], "minted")
        # the same judge saying the goal continues the run binds it
        r2, kw2 = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert kw2["project"] == "client-a" and _meta(r2.handle_id)["project_binding"] == "landscape"

    def test_the_recorded_project_is_a_name_inside_the_projects_root(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import landscape
        from orch_items import projects_root
        root = projects_root()
        root.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "escape").symlink_to(outside, target_is_directory=True)
        assert (root / "escape").is_dir(), "the fixture: is_dir() follows the link"
        (root / "board-reports").mkdir()
        (root / "17").mkdir()

        def rec(hid, **extra):
            return {"relation": "related", "chosen": hid, "continues": True, **extra}

        # a symlink out of the root is not a project
        a = _finished_run(GOAL_HAIKU, "leaves", extra={"project": "escape"})
        assert landscape.chosen_project(rec(a)) == ""
        # a recorded non-string is rejected, never coerced into a directory's name
        for bad in (17, True, ["board-reports"], {"name": "board-reports"}):
            b = _finished_run(GOAL_HAIKU, "leaves", extra={"project": bad})
            assert landscape.chosen_project(rec(b)) == "", bad
            assert landscape.recorded_project(b) == ""
        assert landscape.project_name("board-reports") == "board-reports"
        for bad in ("", " ", "a/b", "a\\b", ".", "..", 17, None, ["x"]):
            assert landscape.project_name(bad) == "", bad
        # a real directory binds — only when the judge said the goal continues
        c = _finished_run(GOAL_HAIKU, "leaves", extra={"project": "board-reports"})
        assert landscape.recorded_project(c) == "board-reports"
        assert landscape.chosen_project(rec(c)) == "board-reports"
        assert landscape.chosen_project({"relation": "related", "chosen": c, "continues": False}) == ""
        assert landscape.chosen_project({"relation": "related", "chosen": c}) == ""  # an older record
        assert landscape.chosen_project({"relation": "related", "chosen": c, "continues": "true"}) == ""
        assert landscape.chosen_project({"relation": "rerun", "chosen": c, "continues": True}) == "board-reports"


class TestProjectBindingProvenance:
    def test_the_navigators_pick_is_recorded_as_the_navigators(self, monkeypatch, tmp_path, caplog):
        # handle_queue passes the navigator's menu pick as `project=`; it is
        # Maro's decision, not an operator's word, and when the landscape
        # would bind elsewhere the disagreement is logged so it can be
        # measured before either is made to outrank the other.
        _setup(monkeypatch, tmp_path)
        from orch_items import projects_root
        (projects_root() / "prior-project").mkdir(parents=True)
        (projects_root() / "nav-project").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "prior-project"})
        origin = {"source": "dispatch", "dispatch_navigator": {"move": "extend", "project": "nav-project"}}
        r, kw = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")),
                            project="nav-project", origin=origin)
        meta = _meta(r.handle_id)
        assert kw["project"] == "nav-project"
        assert (meta["project"], meta["project_binding"]) == ("nav-project", "navigator")
        assert meta["landscape"]["chosen"] and meta["landscape"]["continues"] is True
        assert any("nav-project (navigator) outranks the landscape's prior-project" in m
                   for m in caplog.messages)
        # an operator's explicit project that is not the navigator's pick stays `operator`
        r2, _ = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")),
                            project="prior-project", origin={"source": "dispatch",
                                                             "dispatch_navigator": {"move": "extend"}})
        assert _meta(r2.handle_id)["project_binding"] == "operator"

    def test_the_fallback_rule_is_read_off_the_scan_that_picked_the_project(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        calls = []
        real = handle_mod._match_existing_project

        def counted(message, exclude=()):
            calls.append(message)
            return real(message, exclude)

        monkeypatch.setattr(handle_mod, "_match_existing_project", counted)
        assert handle_mod._project_for_goal("Refresh the board-reports index page") == ("board-reports", "named")
        assert len(calls) == 1
        slug, rule = handle_mod._project_for_goal(GOAL_FOLLOW_UP)
        assert rule == "minted" and slug == handle_mod._default_project_for(GOAL_FOLLOW_UP)
        assert len(calls) == 3  # one per resolution — never a second scan to name the rule

    def test_a_now_answer_binds_no_project(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from handle import handle
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        with _no_hosted_free(), _classify_now():
            r = handle(GOAL_FOLLOW_UP, adapter=_NowAndJudge(_related(1, "carries it forward")),
                       force_lane="now", dry_run=False)
        assert r.status == "done" and r.lane == "now"
        meta = _meta(r.handle_id)
        assert "project_binding" not in meta and "project" not in meta

    def test_a_failed_stamp_is_said_not_swallowed(self, monkeypatch, tmp_path, caplog):
        _setup(monkeypatch, tmp_path)
        import runs
        monkeypatch.setattr(runs, "stamp_run_metadata", lambda fields: None)
        r, kw = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge())
        assert kw["project"]
        assert any("not recorded in run metadata" in m for m in caplog.messages)


class TestTheBoundProjectComposes:
    def test_the_scope_pass_decides_in_the_bound_project(self, monkeypatch, tmp_path):
        # the literal path: landscape → binding → scope generation → the loop;
        # the scope's decision domain is the bound project, not the goal slug
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        import config
        from handle import _default_project_for
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        monkeypatch.setattr(config, "get_bool",
                            lambda key, default=False: True if key == "scope_generation" else default)
        gen = MagicMock(return_value=None)
        with patch("scope.generate_resolved_intent", gen):
            r, kw = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert kw["project"] == "board-reports"
        assert gen.call_count == 1
        assert gen.call_args.kwargs["decision_domain"] == "board-reports" != _default_project_for(GOAL_FOLLOW_UP)

    def test_a_fork_of_a_landscape_bound_run_records_the_real_parent_project(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        from handle import _default_project_for
        from orch_items import projects_root, project_dir
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        parent, kw = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert kw["project"] == "board-reports" != _default_project_for(GOAL_FOLLOW_UP)
        # the parent's run is done; a dispatched child forks from it
        import runs
        runs.stamp_run_metadata_for(parent.handle_id, {"status": "done", "ended_at": "2026-09-13T00:00:00Z"})
        child_goal = "Draft the cover letter for the investor deck"
        rec = MagicMock()
        with patch("ancestry.record_fork_ancestry", rec):
            child, ckw = _agenda_run(monkeypatch, child_goal, _NowAndJudge(),
                                     origin={"source": "dispatch", "parent_handle_id": parent.handle_id,
                                             "parent_goal": GOAL_FOLLOW_UP})
        assert ckw["project"] == _default_project_for(child_goal)
        assert rec.call_count == 1
        assert rec.call_args.kwargs["parent_id"] == "board-reports"
        assert rec.call_args.kwargs["parent_dir"] == project_dir("board-reports")
        assert rec.call_args.args[0] == project_dir(ckw["project"])
        # a parent with no recorded run keeps the goal-text derivation
        rec2 = MagicMock()
        with patch("ancestry.record_fork_ancestry", rec2):
            _agenda_run(monkeypatch, child_goal, _NowAndJudge(),
                        origin={"source": "dispatch", "parent_handle_id": "nope0000", "parent_goal": GOAL_FOLLOW_UP})
        assert rec2.call_args.kwargs["parent_id"] == _default_project_for(GOAL_FOLLOW_UP)


class TestTheFallbacksHonourTheJudge:
    """Review round 2 (2026-09-13): a verdict the judge or the binder made
    must not be undone one layer down by the automatic fallbacks."""

    def test_a_context_only_verdict_keeps_the_minted_slug_out_of_that_project(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import landscape
        from handle import _default_project_for, _project_for_goal
        from loop_artifacts import resolve_project_slug
        from orch_items import projects_root
        goal_a = "Summarize the quarterly revenue report for client A"
        goal_b = "Summarize the quarterly revenue report for client B"
        slug = resolve_project_slug(goal_a)
        (projects_root() / slug).mkdir(parents=True)
        prior = _finished_run(goal_a, "Client A: revenue rose.", extra={"project": slug})
        # the fixture: both goals open the same way, so the minted slug REUSES client A's project
        assert _default_project_for(goal_b) == slug and _project_for_goal(goal_b) == (slug, "minted")
        r, kw = _agenda_run(monkeypatch, goal_b, _NowAndJudge(_related(1, "same method, other client", continues=False)))
        meta = _meta(r.handle_id)
        assert meta["landscape"]["chosen"] == prior and meta["landscape"]["continues"] is False
        assert kw["project"] != slug and kw["project"].startswith(slug + "-")
        assert (meta["project"], meta["project_binding"]) == (kw["project"], "minted")
        # the unit: the context-only project, and the fallback stepping aside from it
        assert landscape.context_only_project(meta["landscape"]) == slug
        assert landscape.context_only_project({**meta["landscape"], "continues": True}) == ""
        assert landscape.context_only_project({"relation": "rerun", "chosen": prior}) == ""
        # (the flow above reserved -2; each allocation reserves the next)
        assert _project_for_goal(goal_b, (slug,)) == (slug + "-3", "minted")
        assert _project_for_goal(goal_b, (slug,)) == (slug + "-4", "minted")

    def test_a_context_only_verdict_keeps_the_named_shortcut_out_of_that_project(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from handle import _match_existing_project
        from orch_items import projects_root
        (projects_root() / "client-a").mkdir(parents=True)
        prior = _finished_run("Write the client-a quarterly report", "Done.", extra={"project": "client-a"})
        goal = "Use the client-a report as a template for client B"
        assert _match_existing_project(goal) == "client-a", "the fixture: the goal names the source project"
        r, kw = _agenda_run(monkeypatch, goal, _NowAndJudge(_related(1, "a template, other work", continues=False)))
        meta = _meta(r.handle_id)
        assert meta["landscape"]["chosen"] == prior
        assert kw["project"] != "client-a" and meta["project_binding"] == "minted"
        # and when the judge says the goal continues that work, the named project it is
        # (a fresh workspace: the run above would otherwise be the newest candidate)
        _setup(monkeypatch, tmp_path / "two")
        (projects_root() / "client-a").mkdir(parents=True)
        _finished_run("Write the client-a quarterly report", "Done.", extra={"project": "client-a"})
        r2, kw2 = _agenda_run(monkeypatch, goal, _NowAndJudge(_related(1, "carries it forward")))
        assert kw2["project"] == "client-a" and _meta(r2.handle_id)["project_binding"] == "landscape"

    def test_a_rejected_symlink_is_not_reselected_by_the_goal_text(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import landscape
        from handle import _match_existing_project, _project_for_goal
        from loop_artifacts import resolve_project_slug
        from orch_items import projects_root
        root = projects_root()
        root.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "client-a").symlink_to(outside, target_is_directory=True)
        assert not landscape.project_inside_root("client-a")
        assert landscape.project_inside_root("not-there") and not landscape.project_inside_root("../x")
        (root / "real-one").mkdir()
        assert landscape.project_inside_root("real-one")
        # the named shortcut skips it ...
        goal = "Extend the client-a report"
        assert _match_existing_project(goal) == ""
        prior = _finished_run("Write the client-a report", "Done.", extra={"project": "client-a"})
        r, kw = _agenda_run(monkeypatch, goal, _NowAndJudge(_related(1, "carries it forward")))
        meta = _meta(r.handle_id)
        assert meta["landscape"]["chosen"] == prior and meta["landscape"]["continues"] is True
        assert kw["project"] != "client-a" and meta["project_binding"] == "minted"
        assert (root / kw["project"]).resolve().is_relative_to(root.resolve()) or not (root / kw["project"]).exists()
        # ... and so does the minted slug when the slug's own directory is a link out of the root
        goal2 = "Extend the client report now"
        slug2 = resolve_project_slug(goal2)
        (root / slug2).symlink_to(outside, target_is_directory=True)
        assert _project_for_goal(goal2) == (slug2 + "-2", "minted")

    def test_a_whitespace_padded_name_is_rejected_not_canonicalised(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import landscape
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        (projects_root() / " board-reports ").mkdir(parents=True)
        assert landscape.project_name(" board-reports ") == "" and landscape.project_name("board-reports\n") == ""
        a = _finished_run(GOAL_HAIKU, "leaves", extra={"project": " board-reports "})
        assert landscape.recorded_project(a) == ""
        assert landscape.chosen_project({"relation": "related", "chosen": a, "continues": True}) == ""


class TestTheDecisionFollowsTheGoalItBindsOn:
    def test_a_clarified_goal_is_judged_again_before_it_binds(self, monkeypatch, tmp_path):
        # the landscape judged the goal AS SUBMITTED; the channel reply names
        # other work — the decision, its origin, its context and the bound
        # project all follow the clarified goal
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        from handle import handle, _default_project_for
        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        from orch_items import projects_root
        import llm
        (projects_root() / "client-a").mkdir(parents=True)
        prior = _finished_run(GOAL_QUARTERLY, "Client A: revenue rose.", extra={"project": "client-a"})
        adapter = _NowAndJudge([_related(1, "carries it forward"),
                                _related(1, "the same method for client B", continues=False)])
        monkeypatch.setattr(llm, "build_adapter", lambda *x, **k: adapter)
        channel = MagicMock()
        channel.ask.return_value = "This is for client B; use a separate workspace."
        loop_kwargs = []

        def _fake_run(g, *x, **k):
            loop_kwargs.append((g, k))
            return LoopResult(loop_id="test-clar", project=k.get("project", ""), goal=g, status="done",
                              stuck_reason=None,
                              steps=[StepOutcome(index=0, text="step", status="done", result="output", iteration=0)])

        gate = MagicMock()
        gate.escalate = False
        gate.contested_claims = []
        with _no_hosted_free(), \
             patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": False, "question": "Which client?"}), \
             patch("director.verify_goal_completion",
                   return_value=ClosureVerdict(complete=True, confidence=0.9, gaps=[],
                                               summary="verified", checks_run=2, checks_passed=2)), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            r = handle(GOAL_FOLLOW_UP, force_lane="agenda", dry_run=False, channel=channel)
        channel.ask.assert_called_once_with("Which client?")
        goal_run, kw = loop_kwargs[0]
        assert "Additional context: This is for client B" in goal_run
        judged = [m[0][-1].content for m in adapter.calls if m[1].get("purpose") == "landscape"]
        assert len(judged) == 2 and "client B" in judged[1] and "client B" not in judged[0]
        meta = _meta(r.handle_id)
        assert meta["landscape"]["chosen"] == prior and meta["landscape"]["continues"] is False
        assert kw["project"] != "client-a" and meta["project_binding"] == "minted"
        assert meta["origin"]["relation"] == "related" and meta["origin"]["parent_handle_id"] == prior

    def test_a_clarified_goal_that_is_fresh_drops_the_first_decisions_origin(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        from handle import handle
        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        from orch_items import projects_root
        import llm
        (projects_root() / "client-a").mkdir(parents=True)
        prior = _finished_run(GOAL_QUARTERLY, "Client A: revenue rose.", extra={"project": "client-a"})
        fresh = json.dumps({"relation": "fresh", "run": 0, "reason": "other work"})
        adapter = _NowAndJudge([_related(1, "carries it forward"), fresh])
        monkeypatch.setattr(llm, "build_adapter", lambda *x, **k: adapter)
        channel = MagicMock()
        channel.ask.return_value = "Not that report — a new one for client B."
        gate = MagicMock()
        gate.escalate = False
        gate.contested_claims = []
        with _no_hosted_free(), \
             patch("agent_loop.run_agent_loop", side_effect=lambda g, *x, **k: LoopResult(
                 loop_id="l", project=k.get("project", ""), goal=g, status="done", stuck_reason=None,
                 steps=[StepOutcome(index=0, text="s", status="done", result="o", iteration=0)])), \
             patch("intent.check_goal_clarity", return_value={"clear": False, "question": "Which?"}), \
             patch("director.verify_goal_completion",
                   return_value=ClosureVerdict(complete=True, confidence=0.9, gaps=[],
                                               summary="verified", checks_run=2, checks_passed=2)), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            r = handle(GOAL_FOLLOW_UP, force_lane="agenda", dry_run=False, channel=channel,
                       origin={"source": "cli"})
        meta = _meta(r.handle_id)
        assert meta["landscape"]["relation"] == "fresh" and meta["landscape"]["chosen"] == ""
        assert meta["origin"] == {"source": "cli"}, meta["origin"]
        assert meta["project_binding"] == "minted"

    def test_an_escalation_changes_the_project_and_says_so(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        from handle import handle
        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        from orch_items import projects_root
        import llm
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        adapter = _NowAndJudge(_related(1, "carries it forward"))
        monkeypatch.setattr(llm, "build_adapter", lambda *x, **k: adapter)
        projects = []

        def _fake_run(g, *x, **k):
            projects.append(k.get("project", ""))
            return LoopResult(loop_id=f"lr-{len(projects)}", project=k.get("project", ""), goal=g,
                              status="done", stuck_reason=None,
                              steps=[StepOutcome(index=0, text="s", status="done", result="o", iteration=0)])

        verdicts = [ClosureVerdict(complete=True, confidence=0.65, gaps=[], summary="weak", checks_run=2, checks_passed=1),
                    ClosureVerdict(complete=True, confidence=0.9, gaps=[], summary="ok", checks_run=2, checks_passed=2)]
        gate = MagicMock()
        gate.escalate = True
        gate.contested_claims = []
        gate.reason = "weak coverage"
        with _no_hosted_free(), \
             patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion", side_effect=lambda *a, **k: verdicts.pop(0)), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            r = handle(GOAL_FOLLOW_UP, force_lane="agenda", model="cheap", dry_run=False)
        assert projects == ["board-reports", "board-reports-escalated"], projects
        meta = _meta(r.handle_id)
        assert (meta["project"], meta["project_binding"]) == ("board-reports-escalated", "escalated")


def _escalating_run(monkeypatch, goal, adapter, second_status="done", **kw):
    """An AGENDA run whose quality gate escalates once: the first loop's
    closure is non-defending (0.65), the retry's is 0.9. Returns
    (HandleResult, [projects the loops ran in])."""
    from unittest.mock import MagicMock
    from handle import handle
    from agent_loop import LoopResult, StepOutcome
    from director import ClosureVerdict
    import llm
    monkeypatch.setattr(llm, "build_adapter", lambda *x, **k: adapter)
    projects = []

    def _fake_run(g, *x, **k):
        projects.append(k.get("project", ""))
        status = "done" if len(projects) == 1 else second_status
        steps = [StepOutcome(index=0, text="s", status="done", result="o", iteration=0)] if status == "done" else []
        return LoopResult(loop_id=f"lr-{len(projects)}", project=k.get("project", ""), goal=g,
                          status=status, stuck_reason=None if status == "done" else "budget", steps=steps)

    verdicts = [ClosureVerdict(complete=True, confidence=0.65, gaps=[], summary="weak", checks_run=2, checks_passed=1),
                ClosureVerdict(complete=True, confidence=0.9, gaps=[], summary="ok", checks_run=2, checks_passed=2)]
    gate = MagicMock()
    gate.escalate = True
    gate.contested_claims = []
    gate.reason = "weak coverage"
    with _no_hosted_free(), \
         patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
         patch("intent.check_goal_clarity", return_value={"clear": True}), \
         patch("director.verify_goal_completion", side_effect=lambda *a, **k: verdicts.pop(0)), \
         patch("quality_gate.run_quality_gate", return_value=gate):
        r = handle(goal, force_lane="agenda", model="cheap", dry_run=False, **kw)
    return r, projects


class TestTheConstraintsSurviveTheTransitions:
    """Review round 3 (2026-09-13): the binding's constraints must hold
    through the automatic transitions after it — the sibling allocator, the
    escalation retry and its revert, the clarified re-decision's origin."""

    def test_the_sibling_allocator_never_returns_the_rejected_base(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        from orch_items import projects_root
        root = projects_root()
        root.mkdir(parents=True, exist_ok=True)
        # a dangling symlink at -2 is not free (exists() would call it absent)
        (root / "client-a-2").symlink_to(tmp_path / "gone")
        assert not (root / "client-a-2").exists() and (root / "client-a-2").is_symlink()
        assert handle_mod._free_project_name("client-a", ("client-a",), "Extend client A's report") == "client-a-3"
        assert (root / "client-a-3").is_dir(), "the name is reserved, not merely observed"
        # the range exhausted: a random suffix, never the base
        monkeypatch.setattr(handle_mod, "_PROJECT_SIBLING_CAP", 4)
        (root / "client-a-4").mkdir()
        name = handle_mod._free_project_name("client-a", ("client-a",), "Extend client A's report")
        assert name != "client-a" and name.startswith("client-a-") and (root / name).is_dir()
        assert len(name) == len("client-a-") + 8
        # even that taken: fail closed
        import uuid
        monkeypatch.setattr(uuid, "uuid4", lambda: type("U", (), {"hex": "deadbeefcafe"})())
        (root / "client-a-deadbeef").mkdir()
        with pytest.raises(RuntimeError):
            handle_mod._free_project_name("client-a", ("client-a",), "Extend client A's report")
        # through the fallback: an excluded slug whose -2 is a dangling link lands in -3
        goal = "Extend the client report now"
        from loop_artifacts import resolve_project_slug
        slug = resolve_project_slug(goal)
        (root / f"{slug}-2").symlink_to(tmp_path / "gone-too")
        assert handle_mod._project_for_goal(goal, (slug,)) == (f"{slug}-3", "minted")

    def test_an_escalation_keeps_out_of_the_context_only_project(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        (projects_root() / "board-reports-escalated").mkdir(parents=True)
        # client A's earlier run escalated into board-reports-escalated; the
        # judge relates client B's goal to it as context only
        prior = _finished_run("Refresh the board-reports index page for client A", "Refreshed.",
                              extra={"project": "board-reports-escalated"})
        goal = "Refresh the board-reports index page for client B"
        r, projects = _escalating_run(monkeypatch, goal,
                                      _NowAndJudge(_related(1, "same page, other client", continues=False)))
        meta = _meta(r.handle_id)
        assert meta["landscape"]["chosen"] == prior and meta["landscape"]["continues"] is False
        assert projects[0] == "board-reports"
        assert projects[1] != "board-reports-escalated" and projects[1].startswith("board-reports-escalated-")
        assert (meta["project"], meta["project_binding"]) == (projects[1], "escalated")

    def test_a_failed_escalation_leaves_the_run_in_the_delivered_project(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import landscape
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        r, projects = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")),
                                      second_status="stuck")
        assert projects == ["board-reports", "board-reports-escalated"]
        assert r.status == "done" and "did not complete" in (r.result or "")
        meta = _meta(r.handle_id)
        assert (meta["project"], meta["project_binding"]) == ("board-reports", "landscape")
        # the next continuation follows the delivered work, not the dead retry
        assert landscape.recorded_project(r.handle_id) == "board-reports"
        # a retry that raises: the pair is restored before the error propagates
        _setup(monkeypatch, tmp_path / "two")
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        from unittest.mock import MagicMock
        from handle import handle
        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        import llm
        adapter = _NowAndJudge(_related(1, "carries it forward"))
        monkeypatch.setattr(llm, "build_adapter", lambda *x, **k: adapter)
        seen = []

        def _fake_run(g, *x, **k):
            seen.append(k.get("project", ""))
            if len(seen) == 2:
                raise RuntimeError("retry blew up")
            return LoopResult(loop_id="lr-1", project=k.get("project", ""), goal=g, status="done", stuck_reason=None,
                              steps=[StepOutcome(index=0, text="s", status="done", result="o", iteration=0)])

        gate = MagicMock()
        gate.escalate = True
        gate.contested_claims = []
        gate.reason = "weak"
        with _no_hosted_free(), \
             patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   return_value=ClosureVerdict(complete=True, confidence=0.65, gaps=[], summary="weak",
                                               checks_run=2, checks_passed=1)), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            try:
                r2 = handle(GOAL_FOLLOW_UP, force_lane="agenda", model="cheap", dry_run=False)
            except RuntimeError:
                r2 = None
        assert seen == ["board-reports", "board-reports-escalated"]
        from runs import runs_root
        run_dirs = [d for d in runs_root().iterdir() if (d / "metadata.json").exists()]
        metas = [json.loads((d / "metadata.json").read_text()) for d in run_dirs]
        mine = [m for m in metas if m.get("prompt") == GOAL_FOLLOW_UP or m.get("project_binding")]
        assert mine and all((m["project"], m["project_binding"]) == ("board-reports", "landscape") for m in mine), mine

    def test_a_fresh_re_decision_with_no_caller_origin_clears_the_first_parent_in_one_write(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        from landscape import apply, decide
        a = _finished_run(GOAL_QUARTERLY, "Revenue rose.")
        b = _finished_run(GOAL_FOLLOW_UP, finished=False)
        first = apply(b, None, decide(GOAL_FOLLOW_UP, handle_id=b, adapter=_Judge(_related(1))))
        assert first["parent_handle_id"] == a
        assert _meta(b)["origin"]["parent_handle_id"] == a
        fresh = decide(GOAL_FOLLOW_UP, handle_id=b, adapter=_Judge(json.dumps({"relation": "fresh", "reason": "no"})))
        # without `replace` an empty origin is not written (the first-decision contract) ...
        assert apply(b, None, fresh) is None and _meta(b)["origin"]["parent_handle_id"] == a
        # ... with it the parent goes in the same write as the record
        writes = []
        real = runs.stamp_run_metadata_for

        def spy(hid, fields):
            writes.append(dict(fields))
            return real(hid, fields)

        monkeypatch.setattr(runs, "stamp_run_metadata_for", spy)
        assert apply(b, None, fresh, replace=True) is None
        assert len(writes) == 1 and writes[0]["origin"] == {} and writes[0]["landscape"]["relation"] == "fresh"
        assert _meta(b)["origin"] == {} and _meta(b)["landscape"]["relation"] == "fresh"
        # a write that fails is a decision that was not made — it raises, nothing derived is published
        monkeypatch.setattr(runs, "stamp_run_metadata_for", lambda *x, **kw: None)
        with pytest.raises(RuntimeError):
            apply(b, None, fresh, replace=True)

    def test_a_clarified_goal_with_no_caller_origin_drops_the_first_parent(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        from handle import handle
        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        from orch_items import projects_root
        import llm
        (projects_root() / "client-a").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Client A: revenue rose.", extra={"project": "client-a"})
        fresh = json.dumps({"relation": "fresh", "run": 0, "reason": "other work"})
        adapter = _NowAndJudge([_related(1, "carries it forward"), fresh])
        monkeypatch.setattr(llm, "build_adapter", lambda *x, **k: adapter)
        channel = MagicMock()
        channel.ask.return_value = "Not that report — a new one for client B."
        gate = MagicMock()
        gate.escalate = False
        gate.contested_claims = []
        with _no_hosted_free(), \
             patch("agent_loop.run_agent_loop", side_effect=lambda g, *x, **k: LoopResult(
                 loop_id="l", project=k.get("project", ""), goal=g, status="done", stuck_reason=None,
                 steps=[StepOutcome(index=0, text="s", status="done", result="o", iteration=0)])), \
             patch("intent.check_goal_clarity", return_value={"clear": False, "question": "Which?"}), \
             patch("director.verify_goal_completion",
                   return_value=ClosureVerdict(complete=True, confidence=0.9, gaps=[],
                                               summary="verified", checks_run=2, checks_passed=2)), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            r = handle(GOAL_FOLLOW_UP, force_lane="agenda", dry_run=False, channel=channel)
        meta = _meta(r.handle_id)
        assert meta["landscape"]["relation"] == "fresh"
        assert "parent_handle_id" not in (meta.get("origin") or {}), meta.get("origin")
        assert meta["project_binding"] == "minted" and meta["project"] != "client-a"


class TestTheDecisionIsATransaction:
    """Review round 4 (2026-09-13): a decision is derived, recorded, then
    installed — all or nothing; a re-decision that cannot be made does not
    leave the clarified goal running on the first one; a free sibling is
    reserved, not merely observed; a path is never suffixed into a name."""

    def test_a_context_read_that_fails_leaves_the_first_decision_whole(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        import landscape
        from handle import handle, _default_project_for
        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        from orch_items import projects_root
        import llm
        (projects_root() / "client-a").mkdir(parents=True)
        (projects_root() / "client-b").mkdir(parents=True)
        a = _finished_run("Write the client-a quarterly report", "Client A done.", extra={"project": "client-a"})
        b = _finished_run("Write the client-b quarterly report", "Client B done.", extra={"project": "client-b"})
        # the judge follows A first, then (clarified) B; the context read for
        # B blows up after the decision is derived
        # the judge names the runs by id (both candidates tie on similarity)
        adapter = _NowAndJudge([json.dumps({"relation": "related", "run": a, "continues": True, "reason": "A"}),
                                json.dumps({"relation": "related", "run": b, "continues": True, "reason": "B"})])
        monkeypatch.setattr(llm, "build_adapter", lambda *x, **k: adapter)
        real_ctx = landscape.related_context
        calls = []

        def flaky_ctx(rec):
            calls.append(rec.get("chosen"))
            if rec.get("chosen") == b:
                raise OSError("run storage unavailable")
            return real_ctx(rec)

        monkeypatch.setattr(landscape, "related_context", flaky_ctx)
        channel = MagicMock()
        channel.ask.return_value = "This is for the second client, a separate workspace."
        loop_kwargs = []

        def _fake_run(g, *x, **k):
            loop_kwargs.append(k)
            return LoopResult(loop_id="l", project=k.get("project", ""), goal=g, status="done", stuck_reason=None,
                              steps=[StepOutcome(index=0, text="s", status="done", result="o", iteration=0)])

        gate = MagicMock()
        gate.escalate = False
        gate.contested_claims = []
        goal = "Write the quarterly report"
        with _no_hosted_free(), \
             patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": False, "question": "Which client?"}), \
             patch("director.verify_goal_completion",
                   return_value=ClosureVerdict(complete=True, confidence=0.9, gaps=[],
                                               summary="verified", checks_run=2, checks_passed=2)), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            r = handle(goal, force_lane="agenda", dry_run=False, channel=channel)
        assert calls == [a, b]
        meta = _meta(r.handle_id)
        # neither decision drives the clarified goal: persisted AND live state agree on "fresh"
        assert meta["landscape"]["relation"] == "fresh" and "re-decision failed" in meta["landscape"]["reason"]
        assert "parent_handle_id" not in (meta.get("origin") or {})
        assert loop_kwargs[0]["project"] not in ("client-a", "client-b")
        assert meta["project_binding"] == "minted"
        assert "## Related prior run" not in (loop_kwargs[0].get("ancestry_context_extra") or "")

    def test_a_replacement_stamp_that_fails_does_not_run_the_clarified_goal_on_the_first_verdict(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        import runs
        from handle import handle
        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        from orch_items import projects_root
        import llm
        (projects_root() / "client-a").mkdir(parents=True)
        prior = _finished_run(GOAL_QUARTERLY, "Client A: revenue rose.", extra={"project": "client-a"})
        adapter = _NowAndJudge([_related(1, "carries it forward"), _related(1, "other client", continues=False)])
        monkeypatch.setattr(llm, "build_adapter", lambda *x, **k: adapter)
        real_stamp = runs.stamp_run_metadata_for
        stamps = []

        def failing_replacement(hid, fields):
            stamps.append(dict(fields))
            if "origin" in fields and fields.get("landscape", {}).get("continues") is False:
                return None  # the replacement write fails
            return real_stamp(hid, fields)

        monkeypatch.setattr(runs, "stamp_run_metadata_for", failing_replacement)
        channel = MagicMock()
        channel.ask.return_value = "This is for client B; use a separate workspace."
        loop_kwargs = []

        def _fake_run(g, *x, **k):
            loop_kwargs.append(k)
            return LoopResult(loop_id="l", project=k.get("project", ""), goal=g, status="done", stuck_reason=None,
                              steps=[StepOutcome(index=0, text="s", status="done", result="o", iteration=0)])

        gate = MagicMock()
        gate.escalate = False
        gate.contested_claims = []
        with _no_hosted_free(), \
             patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": False, "question": "Which client?"}), \
             patch("director.verify_goal_completion",
                   return_value=ClosureVerdict(complete=True, confidence=0.9, gaps=[],
                                               summary="verified", checks_run=2, checks_passed=2)), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            r = handle(GOAL_FOLLOW_UP, force_lane="agenda", dry_run=False, channel=channel)
        assert any(s.get("landscape", {}).get("continues") is False for s in stamps), "the replacement was attempted"
        assert loop_kwargs[0]["project"] != "client-a"
        meta = _meta(r.handle_id)
        assert meta["project"] != "client-a" and meta["project_binding"] == "minted"
        assert meta["landscape"]["relation"] == "fresh" and not meta["landscape"].get("chosen")
        assert "re-decision failed" in meta["landscape"]["reason"]
        assert "parent_handle_id" not in (meta.get("origin") or {}), meta.get("origin")
        assert prior not in (loop_kwargs[0].get("ancestry_context_extra") or "")

    def test_a_free_sibling_is_reserved_not_observed(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        from orch_items import projects_root
        root = projects_root()
        # two pending runs that both saw -2 vacant get two names
        first = handle_mod._free_project_name("client-a", ("client-a",), "Extend client A's report")
        second = handle_mod._free_project_name("client-a", ("client-a",), "Extend client A's report")
        assert (first, second) == ("client-a-2", "client-a-3")
        assert (root / first).is_dir() and (root / second).is_dir()
        # through the binding: two same-opening goals under a context-only verdict land apart
        goal_b = "Summarize the quarterly revenue report for client B"
        goal_c = "Summarize the quarterly revenue report for client C"
        from loop_artifacts import resolve_project_slug
        slug = resolve_project_slug(goal_b)
        assert resolve_project_slug(goal_c) == slug
        pb = handle_mod._project_for_goal(goal_b, (slug,))
        pc = handle_mod._project_for_goal(goal_c, (slug,))
        assert pb != pc and pb[1] == pc[1] == "minted"

    def test_a_path_is_never_suffixed_into_a_name(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        from orch_items import projects_root
        for bad in ("../outside", "/tmp/x", "a/b", " padded ", ""):
            with pytest.raises(ValueError):
                handle_mod._free_project_name(bad, (), "a goal")
        assert not (tmp_path / "outside-2").exists() and not (projects_root() / "outside-2").exists()
        # an escalation of a path-shaped OPERATOR project stays beside it (the override), never elsewhere
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        r, projects = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")),
                                      project="../outside")
        assert projects == ["../outside", "../outside-escalated"], projects
        meta = _meta(r.handle_id)
        assert (meta["project"], meta["project_binding"]) == ("../outside-escalated", "escalated")
        assert not any(p.name.startswith("outside-escalated-") for p in tmp_path.iterdir())


class TestTheBindingReadsASettledWorld:
    """Review round 5 (2026-09-13): the landscape decides over runs whose
    verdict is final, binds to the project the judge saw, reserves a
    sibling WITH its mission, and is not un-made by its own reporting."""

    def test_a_run_whose_verdict_is_still_owed_is_not_yet_a_candidate(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import landscape
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        (projects_root() / "board-reports-escalated").mkdir(parents=True)
        # the answer-first early close: status done, ended_at stamped, the
        # verdict owed — and the gate's escalation has moved the project
        pending = {"since": "2026-09-13T00:00:00+00:00", "loop_id": "lr-1"}
        prior = _finished_run(GOAL_QUARTERLY, "Revenue rose.",
                              extra={"project": "board-reports-escalated", "verdict_pending": pending})
        assert landscape.candidates(GOAL_FOLLOW_UP) == ([], 0, 0)
        assert landscape.run_settled({"verdict_pending": pending}) is False
        # a forged or broken marker cannot hold a finished run out forever
        for shape in ("true", 1, [], {"since": "x", "resolved_at": "2026-09-13T00:01:00+00:00"}):
            assert landscape.run_settled({"verdict_pending": shape}) is True, shape
        assert landscape.run_settled({}) is True
        # through the handle: a follow-up during the window runs fresh,
        # and never lands in the provisional retry workspace
        r, kw = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        meta = _meta(r.handle_id)
        assert meta["landscape"]["relation"] == "fresh" and meta["landscape"]["rule"] == "no_candidates"
        assert kw["project"] not in ("board-reports", "board-reports-escalated")
        assert meta["project_binding"] == "minted"
        # the finalize resolves the marker (the retry failed; the pair is
        # restored): the run settles and the next follow-up continues it
        runs.stamp_run_metadata_for(prior, {"project": "board-reports",
                                            "verdict_pending": {**pending, "resolved_at": "2026-09-13T00:05:00+00:00"}})
        cands, scanned, _ = landscape.candidates(GOAL_FOLLOW_UP, exclude_handle_id=r.handle_id)
        assert scanned == 1 and [c["handle_id"] for c in cands] == [prior] and cands[0]["project"] == "board-reports"
        r2, kw2 = _agenda_run(monkeypatch, GOAL_FOLLOW_UP + " and headcount",
                              _NowAndJudge(json.dumps({"relation": "related", "run": prior, "continues": True,
                                                       "reason": "carries it forward"})))
        meta2 = _meta(r2.handle_id)
        assert meta2["landscape"]["chosen"] == prior
        assert (kw2["project"], meta2["project_binding"]) == ("board-reports", "landscape")

    def test_the_binding_follows_the_snapshot_the_judge_decided_over(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import landscape
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        (projects_root() / "board-reports-escalated").mkdir(parents=True)
        prior = _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        rec = landscape.decide(GOAL_FOLLOW_UP, handle_id="h1", adapter=_Judge(_related(1, "carries it forward")))
        assert rec["chosen"] == prior and rec["candidates"][0]["project"] == "board-reports"
        # the run's metadata moves under the decision: the binding does not
        runs.stamp_run_metadata_for(prior, {"project": "board-reports-escalated"})
        assert landscape.recorded_project(prior) == "board-reports-escalated"
        assert landscape.chosen_project(rec) == "board-reports"
        ctx_only = landscape.decide(GOAL_FOLLOW_UP, handle_id="h2",
                                    adapter=_Judge(_related(1, "other client", continues=False)))
        assert landscape.context_only_project(ctx_only) == "board-reports-escalated"  # what THIS judge saw
        runs.stamp_run_metadata_for(prior, {"project": "board-reports"})
        assert landscape.context_only_project(ctx_only) == "board-reports-escalated"
        # the snapshot is still subject to containment: gone, or a link out
        import shutil
        shutil.rmtree(projects_root() / "board-reports")
        assert landscape.chosen_project(rec) == ""
        (projects_root() / "board-reports").symlink_to(tmp_path)
        assert landscape.chosen_project(rec) == ""
        # a record without a snapshot (hand-built, or template 3) reads the run
        (projects_root() / "board-reports").unlink()
        (projects_root() / "board-reports").mkdir()
        bare = {"relation": "related", "chosen": prior, "continues": True}
        assert landscape.chosen_project(bare) == "board-reports"
        old = {**bare, "candidates": [{"handle_id": prior, "goal": GOAL_QUARTERLY, "similarity": 0.9, "status": "done"}]}
        assert landscape.chosen_project(old) == "board-reports"
        # a snapshot that recorded NO project stays empty even if the run gained one
        none = {**bare, "candidates": [{"handle_id": prior, "project": ""}]}
        assert landscape.chosen_project(none) == "" and landscape.context_only_project({**none, "continues": False}) == ""

    def test_a_reserved_sibling_records_its_mission(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        from loop_artifacts import resolve_project_slug, _recorded_mission
        from orch_items import projects_root, ensure_project
        goal_a = "Tell me about the book Systemantics"
        goal_b = "Tell me about the book Notes on the Synthesis of Form"
        goal_c = "Tell me about the book Chaos by James Gleick"
        base = resolve_project_slug(goal_a)
        assert base == "tell-me-about-the-book"
        ensure_project(base, goal_a)
        # goal B's run steps aside from the base (the judge said context only)
        reserved = handle_mod._free_project_name(base, (base,), goal_b)
        assert reserved == f"{base}-2" and (projects_root() / reserved).is_dir()
        assert _recorded_mission(reserved) == goal_b
        # an unrelated goal that opens the same way does not inherit the reservation
        assert resolve_project_slug(goal_c) == f"{base}-3"
        # ... while goal B itself re-enters its own project
        assert resolve_project_slug(goal_b) == reserved
        # the mission is the loop's own (goal[:80]), so loop init is a no-op over it
        long_goal = "Tell me about the book " + "x" * 100
        r2 = handle_mod._free_project_name(base, (base,), long_goal)
        assert _recorded_mission(r2) == long_goal[:80]
        # a reservation without its mission is refused
        for bad in ("", "   "):
            with pytest.raises(ValueError):
                handle_mod._free_project_name(base, (base,), bad)
        assert not (projects_root() / f"{base}-4").exists()
        # through the binding: goal C after the reservations lands apart from them all
        pc = handle_mod._project_for_goal(goal_c, (base,))
        assert pc == (f"{base}-4", "minted") and pc[0] not in (reserved, r2)

    def test_a_diagnostic_that_fails_after_the_commit_leaves_the_decision_whole(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        prior = _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        real_info = handle_mod.log.info
        raised = []

        def broken_info(msg, *a, **k):
            if isinstance(msg, str) and msg.startswith("landscape:"):
                raised.append(msg)
                raise BrokenPipeError("stderr closed")
            return real_info(msg, *a, **k)

        monkeypatch.setattr(handle_mod.log, "info", broken_info)
        r, kw = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert raised, "the diagnostic ran and failed"
        meta = _meta(r.handle_id)
        # persisted, installed, and driving the run — all three agree
        assert meta["landscape"]["relation"] == "related" and meta["landscape"]["chosen"] == prior
        assert meta["origin"]["parent_handle_id"] == prior
        assert (kw["project"], meta["project"], meta["project_binding"]) == ("board-reports", "board-reports", "landscape")
        assert "## Related prior run" in (kw.get("ancestry_context_extra") or "")


def _dead_pid():
    """A pid that certainly does not exist: spawn-and-reap a child."""
    import subprocess
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


class TestTheSettledWorldSurvivesCrashesAndRaces:
    """Review round 6 (2026-09-13): the record settles on the DELIVERED
    project even when the process dies mid-escalation; a reservation is
    published complete or not at all; a re-decision's own reporting cannot
    un-make it."""

    @pytest.mark.parametrize("verdict_source", ["closure", None])
    def test_a_crash_mid_escalation_settles_on_the_delivered_project(self, monkeypatch, tmp_path, verdict_source):
        _setup(monkeypatch, tmp_path)
        import runs
        import landscape
        import notify
        from audit_repair import sweep_verdict_orphans
        from orch_items import projects_root
        monkeypatch.setattr(notify, "emit", lambda *a, **kw: True)
        (projects_root() / "board-reports").mkdir(parents=True)
        (projects_root() / "board-reports-escalated").mkdir(parents=True)
        # the process died after the escalation moved the project and
        # before the retry delivered: the marker is active, the transition
        # is active, the record says the retry's project
        extra = {"project": "board-reports-escalated", "project_binding": "escalated",
                 "loop_ids": ["lr-1"],
                 "verdict_pending": {"since": "2026-09-13T00:00:00+00:00", "loop_id": "lr-1", "notified_early": True},
                 "project_transition": {"kind": "escalation", "from": "board-reports", "from_binding": "landscape",
                                        "to": "board-reports-escalated", "since": "2026-09-13T00:00:01+00:00"}}
        if verdict_source:
            extra["goal_verdict_source"] = verdict_source
            extra["goal_achieved"] = True
        prior = _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra=extra)
        runs.stamp_run_metadata_for(prior, {"pid": _dead_pid()})
        assert landscape.run_settled(_meta(prior)) is False
        assert landscape.candidates(GOAL_FOLLOW_UP) == ([], 0, 0)
        res = sweep_verdict_orphans(grace_s=0)
        assert res["status"] == "completed" and res["stamped"] == 1, res
        meta = _meta(prior)
        assert meta["verdict_pending"]["resolved_at"]
        # the delivered work is where it was before the move — restored in
        # the same write that settled the run
        assert (meta["project"], meta["project_binding"]) == ("board-reports", "landscape")
        t = meta["project_transition"]
        assert t["outcome"] == "reverted" and t["settled_at"] and t["settled_by"] == "verdict_orphan_sweep"
        if verdict_source:
            assert meta["goal_verdict_source"] == "closure" and meta["goal_achieved"] is True
        assert landscape.run_settled(meta) is True
        cands, scanned, _ = landscape.candidates(GOAL_FOLLOW_UP)
        assert scanned == 1 and cands[0]["handle_id"] == prior and cands[0]["project"] == "board-reports"
        r, kw = _agenda_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert (kw["project"], _meta(r.handle_id)["project_binding"]) == ("board-reports", "landscape")
        # a resolved marker with the transition still active is not settled either
        assert landscape.run_settled({"verdict_pending": {"since": "x", "resolved_at": "y"},
                                      "project_transition": {"from": "a", "to": "a-escalated"}}) is False
        assert landscape.run_settled({"project_transition": {"from": "a", "to": "b", "settled_at": "z"}}) is True
        assert landscape.run_settled({"project_transition": "junk"}) is True
        # settling a transition whose `from` is an operator path keeps the path as given
        fields = landscape.settle_project_transition(
            {"project_transition": {"from": "../outside", "from_binding": "operator", "to": "../outside-escalated"}},
            by="test")
        assert (fields["project"], fields["project_binding"]) == ("../outside", "operator")
        assert landscape.settle_project_transition({"project_transition": {"from": "a", "settled_at": "z"}}, by="t") == {}

    def test_the_escalation_records_its_transition_before_moving(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        real = runs.stamp_run_metadata
        writes = []

        def spy(fields):
            writes.append(dict(fields))
            return real(fields)

        monkeypatch.setattr(runs, "stamp_run_metadata", spy)
        # adopted: the retry delivered
        r, projects = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert projects == ["board-reports", "board-reports-escalated"]
        moves = [w for w in writes if w.get("project") == "board-reports-escalated"]
        # the FIRST write that moves the project carries the active transition
        assert moves and moves[0]["project_transition"]["from"] == "board-reports"
        assert moves[0]["project_transition"]["from_binding"] == "landscape"
        assert "settled_at" not in moves[0]["project_transition"]
        meta = _meta(r.handle_id)
        assert (meta["project"], meta["project_binding"]) == ("board-reports-escalated", "escalated")
        assert meta["project_transition"]["outcome"] == "adopted" and meta["project_transition"]["settled_at"]
        # reverted: the retry died — the pair and the settled transition in ONE write
        _setup(monkeypatch, tmp_path / "two")
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        writes.clear()
        r2, projects2 = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")),
                                        second_status="stuck")
        assert projects2 == ["board-reports", "board-reports-escalated"]
        restore = [w for w in writes if w.get("project") == "board-reports" and "project_transition" in w]
        assert restore and restore[-1]["project_transition"]["outcome"] == "reverted"
        meta2 = _meta(r2.handle_id)
        assert (meta2["project"], meta2["project_binding"]) == ("board-reports", "landscape")
        assert meta2["project_transition"]["settled_at"]
        # not recordable: the retry is not started; the delivered work stands
        _setup(monkeypatch, tmp_path / "three")
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})

        def refusing(fields):
            t = fields.get("project_transition")
            if isinstance(t, dict) and "settled_at" not in t:
                return None
            return real(fields)

        monkeypatch.setattr(runs, "stamp_run_metadata", refusing)
        r3, projects3 = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert projects3 == ["board-reports"], projects3
        assert r3.status == "done" and "not started" in (r3.result or "")
        meta3 = _meta(r3.handle_id)
        assert (meta3["project"], meta3["project_binding"]) == ("board-reports", "landscape")
        assert "project_transition" not in meta3

    def test_a_reservation_is_published_complete_or_not_at_all(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import os
        from pathlib import Path
        import handle as handle_mod
        import orch_items
        from loop_artifacts import resolve_project_slug, _recorded_mission
        from orch_items import projects_root, ensure_project
        goal_a = "Tell me about the book Systemantics"
        goal_b = "Tell me about the book Notes on the Synthesis of Form"
        goal_c = "Tell me about the book Chaos by James Gleick"
        base = "tell-me-about-the-book"
        ensure_project(base, goal_a)
        root = projects_root()
        real_rename = os.rename
        seen = []

        def observing_rename(src, dst):
            # at the instant of publication: the name is still free to every
            # reader, and what is about to appear already carries its mission
            src, dst = str(src), str(dst)
            seen.append((os.path.basename(src), os.path.basename(dst), os.path.exists(dst),
                         resolve_project_slug(goal_c)))
            assert (root / os.path.basename(src) / "NEXT.md").read_text(encoding="utf-8").count(f"> {goal_b}") == 1
            return real_rename(src, dst)

        monkeypatch.setattr(os, "rename", observing_rename)
        reserved = handle_mod._free_project_name(base, (base,), goal_b)
        assert reserved == f"{base}-2"
        assert seen == [(seen[0][0], f"{base}-2", False, f"{base}-2")] and seen[0][0].startswith(".reserve-")
        assert _recorded_mission(reserved) == goal_b
        assert (root / reserved / "NEXT.md").read_text(encoding="utf-8").startswith(f"# NEXT — {reserved}\n")
        assert resolve_project_slug(goal_c) == f"{base}-3"
        assert not [p for p in root.iterdir() if p.name.startswith(".reserve-")]
        monkeypatch.setattr(os, "rename", real_rename)
        # a populated directory that appeared between the free check and the
        # publication is not replaced: the reservation moves on
        def racing_rename(src, dst):
            if os.path.basename(str(dst)) == f"{base}-3" and not os.path.exists(dst):
                os.mkdir(dst)
                (Path(dst) / "notes.md").write_text("theirs", encoding="utf-8")
            return real_rename(src, dst)

        monkeypatch.setattr(os, "rename", racing_rename)
        third = handle_mod._free_project_name(base, (base,), goal_c)
        assert third == f"{base}-4" and (root / f"{base}-3" / "notes.md").read_text(encoding="utf-8") == "theirs"
        assert _recorded_mission(third) == goal_c
        assert not [p for p in root.iterdir() if p.name.startswith(".reserve-")]
        monkeypatch.setattr(os, "rename", real_rename)
        # an initialisation that fails leaves nothing behind and propagates
        def failing_ensure(slug, mission, priority=0):
            raise OSError("project store unwritable")

        monkeypatch.setattr(orch_items, "ensure_project", failing_ensure)
        before = sorted(p.name for p in root.iterdir())
        with pytest.raises(OSError):
            handle_mod._free_project_name(base, (base,), "Tell me about the book Gödel, Escher, Bach")
        assert sorted(p.name for p in root.iterdir()) == before

    def test_a_clarified_re_decision_survives_its_own_diagnostic(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        import handle as handle_mod
        from handle import handle
        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        from orch_items import projects_root
        import llm
        (projects_root() / "client-a").mkdir(parents=True)
        (projects_root() / "client-b").mkdir(parents=True)
        a = _finished_run("Write the client-a quarterly report", "Client A done.", extra={"project": "client-a"})
        b = _finished_run("Write the client-b quarterly report", "Client B done.", extra={"project": "client-b"})
        adapter = _NowAndJudge([
            json.dumps({"relation": "related", "run": a, "continues": True, "reason": "carries A forward"}),
            json.dumps({"relation": "related", "run": b, "continues": True, "reason": "carries B forward"})])
        monkeypatch.setattr(llm, "build_adapter", lambda *x, **k: adapter)
        real_info = handle_mod.log.info
        raised = []

        def broken_info(msg, *x, **k):
            if isinstance(msg, str) and msg.startswith("landscape:"):
                raised.append(msg)
                raise BrokenPipeError("stderr closed")
            return real_info(msg, *x, **k)

        monkeypatch.setattr(handle_mod.log, "info", broken_info)
        channel = MagicMock()
        channel.ask.return_value = "This is for the second client, a separate workspace."
        loop_kwargs = []

        def _fake_run(g, *x, **k):
            loop_kwargs.append(k)
            return LoopResult(loop_id="l", project=k.get("project", ""), goal=g, status="done", stuck_reason=None,
                              steps=[StepOutcome(index=0, text="s", status="done", result="o", iteration=0)])

        gate = MagicMock()
        gate.escalate = False
        gate.contested_claims = []
        with _no_hosted_free(), \
             patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": False, "question": "Which client?"}), \
             patch("director.verify_goal_completion",
                   return_value=ClosureVerdict(complete=True, confidence=0.9, gaps=[],
                                               summary="verified", checks_run=2, checks_passed=2)), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            r = handle("Write the quarterly report", force_lane="agenda", dry_run=False, channel=channel)
        assert any(m.startswith("landscape: re-decided") for m in raised), raised
        meta = _meta(r.handle_id)
        # the clarified decision — persisted, installed, driving the run
        assert meta["landscape"]["relation"] == "related" and meta["landscape"]["chosen"] == b
        assert meta["origin"]["parent_handle_id"] == b
        assert (loop_kwargs[0]["project"], meta["project"], meta["project_binding"]) == ("client-b", "client-b", "landscape")


class TestTheTransitionHasOneLifecycle:
    """Review round 7 (2026-09-13): from the transition write to the retry's
    return there is one revert path; a settlement the run could not record
    is retried at the finalize; a transition orphaned without a verdict
    marker is reverted by its own sweep; a partial reservation leaves no
    staging behind."""

    def test_a_failure_before_the_retry_reverts_the_transition(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        import handle as handle_mod
        import landscape
        from handle import handle
        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        from orch_items import projects_root
        import llm
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        adapter = _NowAndJudge(_related(1, "carries it forward"))
        from quality_gate import next_model_tier
        retry_tier = next_model_tier("cheap")
        built = []

        def factory(*x, **k):
            built.append(k.get("model"))
            if k.get("model") == retry_tier:
                raise RuntimeError("adapter unavailable")
            return adapter

        monkeypatch.setattr(llm, "build_adapter", factory)
        projects = []

        def _fake_run(g, *x, **k):
            projects.append(k.get("project", ""))
            return LoopResult(loop_id="lr-1", project=k.get("project", ""), goal=g, status="done", stuck_reason=None,
                              steps=[StepOutcome(index=0, text="s", status="done", result="o", iteration=0)])

        gate = MagicMock()
        gate.escalate = True
        gate.contested_claims = []
        gate.reason = "weak"
        with _no_hosted_free(), \
             patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   return_value=ClosureVerdict(complete=True, confidence=0.65, gaps=[], summary="weak",
                                               checks_run=2, checks_passed=1)), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            r = handle(GOAL_FOLLOW_UP, force_lane="agenda", model="cheap", dry_run=False)
        assert retry_tier in built, built
        assert projects == ["board-reports"] and r.status == "done"
        meta = _meta(r.handle_id)
        assert (meta["project"], meta["project_binding"]) == ("board-reports", "landscape")
        t = meta["project_transition"]
        assert t["outcome"] == "reverted" and t["settled_at"] and t["to"] == "board-reports-escalated"
        assert landscape.run_settled(meta) is True
        # the settled run is a candidate again, with the delivered project
        cands, _, _ = landscape.candidates(GOAL_FOLLOW_UP + " and headcount")
        assert any(c["handle_id"] == r.handle_id and c["project"] == "board-reports" for c in cands)

    def test_a_settlement_the_run_could_not_record_is_retried_at_the_finalize(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import landscape
        import handle as handle_mod
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        real = runs.stamp_run_metadata
        refused = []

        def refusing_settlements(fields):
            t = fields.get("project_transition")
            if isinstance(t, dict) and t.get("settled_at"):
                refused.append(t["outcome"])
                return None
            return real(fields)

        monkeypatch.setattr(runs, "stamp_run_metadata", refusing_settlements)
        r, projects = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert projects == ["board-reports", "board-reports-escalated"]
        assert refused == ["adopted", "adopted"], refused  # two attempts in the run
        meta = _meta(r.handle_id)
        # the finalize carried the settlement in the marker's resolving write
        assert (meta["project"], meta["project_binding"]) == ("board-reports-escalated", "escalated")
        assert meta["project_transition"]["outcome"] == "adopted" and meta["project_transition"]["settled_at"]
        assert landscape.run_settled(meta) is True
        assert r.handle_id not in handle_mod._UNSETTLED_TRANSITIONS
        # the reverting settlement takes the same road
        _setup(monkeypatch, tmp_path / "two")
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        refused.clear()
        r2, projects2 = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")),
                                        second_status="stuck")
        assert projects2 == ["board-reports", "board-reports-escalated"] and refused == ["reverted", "reverted"]
        meta2 = _meta(r2.handle_id)
        assert (meta2["project"], meta2["project_binding"]) == ("board-reports", "landscape")
        assert meta2["project_transition"]["outcome"] == "reverted" and landscape.run_settled(meta2) is True

    def test_a_transition_orphaned_without_a_marker_is_reverted_by_its_sweep(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import os
        import inspect
        from datetime import datetime, timezone
        import runs
        import landscape
        import heartbeat
        from audit_repair import sweep_transition_orphans
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        (projects_root() / "board-reports-escalated").mkdir(parents=True)
        active = {"kind": "escalation", "from": "board-reports", "from_binding": "landscape",
                  "to": "board-reports-escalated", "since": "2026-09-13T00:00:01+00:00"}
        base = {"project": "board-reports-escalated", "project_binding": "escalated", "project_transition": active}
        # (i) no marker at all (verdict follow-up off), owner dead
        no_marker = _finished_run(GOAL_QUARTERLY, "A.", extra=dict(base))
        # (ii) a RESOLVED marker, owner dead: the verdict sweep will never look
        resolved = _finished_run(GOAL_QUARTERLY + " again", "B.", extra={
            **base, "verdict_pending": {"since": "2026-09-13T00:00:00+00:00", "resolved_at": "2026-09-13T00:10:00+00:00"}})
        # (iii) an ACTIVE marker: the verdict sweep's, not this one's
        marked = _finished_run(GOAL_QUARTERLY + " thrice", "C.", extra={
            **base, "verdict_pending": {"since": "2026-09-13T00:00:00+00:00", "loop_id": "x"}})
        # (iv) too young, (v) the recorded owner ALIVE — the host process
        # of a handle that has finished (a long-lived worker); its
        # liveness is not ownership here (review r8)
        young = _finished_run(GOAL_QUARTERLY + " four", "D.", extra={
            **base, "project_transition": {**active, "since": datetime.now(timezone.utc).isoformat()}})
        alive = _finished_run(GOAL_QUARTERLY + " five", "E.", extra=dict(base))
        for hid in (no_marker, resolved, marked, young):
            runs.stamp_run_metadata_for(hid, {"pid": _dead_pid()})
        runs.stamp_run_metadata_for(alive, {"pid": os.getpid()})
        for hid in (no_marker, resolved, marked, young, alive):
            assert landscape.run_settled(_meta(hid)) is False
        res = sweep_transition_orphans(grace_s=60)
        assert res == {"status": "completed", "stamped": 3, "considered": 4, "retried": 0, "dropped": 0}, res
        for hid in (no_marker, resolved, alive):
            m = _meta(hid)
            assert (m["project"], m["project_binding"]) == ("board-reports", "landscape")
            assert m["project_transition"]["outcome"] == "reverted"
            assert m["project_transition"]["settled_by"] == "transition_orphan_sweep"
            assert landscape.run_settled(m) is True
        for hid in (marked, young):
            m = _meta(hid)
            assert m["project"] == "board-reports-escalated" and "settled_at" not in m["project_transition"]
        # the sweep runs where the verdict sweep runs
        assert "sweep_transition_orphans" in inspect.getsource(heartbeat)

    def test_a_partial_initialisation_leaves_no_staging_behind(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import file_lock
        from pathlib import Path
        import handle as handle_mod
        from orch_items import projects_root, ensure_project
        base = "tell-me-about-the-book"
        ensure_project(base, "Tell me about the book Systemantics")
        root = projects_root()
        real_write = file_lock.atomic_write
        written = []

        def failing_after_next(path, *a, **k):
            written.append(Path(path).name)
            if Path(path).name == "DECISIONS.md":
                raise OSError("disk full after NEXT.md")
            return real_write(path, *a, **k)

        monkeypatch.setattr(file_lock, "atomic_write", failing_after_next)
        before = sorted(p.name for p in root.iterdir())
        with pytest.raises(OSError):
            handle_mod._free_project_name(base, (base,), "Tell me about the book Chaos")
        assert "NEXT.md" in written and "DECISIONS.md" in written
        assert sorted(p.name for p in root.iterdir()) == before
        assert not [p for p in root.iterdir() if p.name.startswith(".reserve-")]


class TestRecoveryOutlivesTheHandle:
    """Review round 8 (2026-09-13): a settlement the finalize could not
    write is kept and drained by the sweep in the same process; the sweep
    runs even when the verdict sweep raises; a pid that cannot exist does
    not abort the verdict sweep; a queued RESUME keeps the run's project."""

    def test_a_settlement_the_finalize_could_not_write_is_kept_and_drained(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import landscape
        import handle as handle_mod
        from audit_repair import sweep_transition_orphans
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        real = runs.stamp_run_metadata
        real_for = runs.revise_run_metadata_for
        refused = []

        def settled(fields):
            t = fields.get("project_transition")
            return isinstance(t, dict) and bool(t.get("settled_at"))

        def refusing(fields):
            if settled(fields):
                refused.append("run")
                return None
            return real(fields)

        def refusing_for(hid, fn):
            if settled(fn(_meta(hid))):
                refused.append("finalize")
                return None
            return real_for(hid, fn)

        monkeypatch.setattr(runs, "stamp_run_metadata", refusing)
        monkeypatch.setattr(runs, "revise_run_metadata_for", refusing_for)
        r, projects = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert projects == ["board-reports", "board-reports-escalated"]
        assert refused == ["run", "run", "finalize"], refused
        # kept WHOLE, not dropped — the intended outcome AND the finalize's
        # obligation to resolve the marker (review r9/r10: the resolution is
        # materialised from the store's snapshot at the drain, not carried)
        kept = handle_mod._UNSETTLED_TRANSITIONS.get(r.handle_id)
        assert kept and kept["project_transition"]["outcome"] == "adopted"
        assert (kept["project"], kept["project_binding"]) == ("board-reports-escalated", "escalated")
        assert kept["_finalize"] is True and "verdict_pending" not in kept
        meta = _meta(r.handle_id)
        assert "settled_at" not in meta["project_transition"] and landscape.run_settled(meta) is False
        # the store comes back; the sweep in this process writes the kept
        # settlement first, even with nothing aged on disk and the owner alive
        monkeypatch.setattr(runs, "revise_run_metadata_for", real_for)
        res = sweep_transition_orphans(grace_s=10 ** 9)
        assert res["status"] == "completed" and res["retried"] == 1 and res["stamped"] == 0, res
        assert r.handle_id not in handle_mod._UNSETTLED_TRANSITIONS
        meta = _meta(r.handle_id)
        assert (meta["project"], meta["project_binding"]) == ("board-reports-escalated", "escalated")
        assert meta["project_transition"]["outcome"] == "adopted" and meta["project_transition"]["settled_at"]
        # nothing is left open: the marker is resolved by the same drain,
        # the world is settled and the run is a candidate again, at the
        # retry's project, while its worker is still alive (review r9)
        assert meta["verdict_pending"]["resolved_at"]
        assert landscape.run_settled(meta) is True
        cands, _, _ = landscape.candidates(GOAL_FOLLOW_UP + " and headcount")
        assert any(c["handle_id"] == r.handle_id and c["project"] == "board-reports-escalated" for c in cands)
        # a second sweep has nothing to retry
        assert sweep_transition_orphans(grace_s=10 ** 9)["retried"] == 0

    def test_the_transition_sweep_runs_when_the_verdict_sweep_raises(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import audit_repair
        import heartbeat as hb
        calls = []

        def boom(**kw):
            raise OverflowError("Python int too large to convert to C int")

        monkeypatch.setattr(audit_repair, "sweep_verdict_orphans", boom)
        monkeypatch.setattr(audit_repair, "sweep_transition_orphans",
                            lambda **kw: calls.append(kw) or {"status": "completed", "stamped": 1, "retried": 2,
                                                              "considered": 1})
        result = hb.stranded_state_sweep()
        assert calls and calls[0].get("limit") == 5
        assert result.get("transition_orphans_settled") == 3
        assert "verdict_orphans_stamped" not in result

    def test_a_pid_that_cannot_exist_does_not_abort_the_verdict_sweep(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import notify
        from audit_repair import sweep_verdict_orphans
        monkeypatch.setattr(notify, "emit", lambda *a, **kw: True)
        marker = {"since": "2026-09-13T00:00:00+00:00", "loop_id": "lr-1", "notified_early": True}
        absurd = _finished_run(GOAL_QUARTERLY, "A.", extra={"loop_ids": ["lr-1"], "verdict_pending": dict(marker),
                                                             "goal_verdict_source": "closure", "goal_achieved": True})
        dead = _finished_run(GOAL_QUARTERLY + " again", "B.", extra={"loop_ids": ["lr-2"], "verdict_pending": dict(marker),
                                                                      "goal_verdict_source": "closure", "goal_achieved": True})
        runs.stamp_run_metadata_for(absurd, {"pid": 2 ** 80})
        runs.stamp_run_metadata_for(dead, {"pid": _dead_pid()})
        res = sweep_verdict_orphans(grace_s=0)
        assert res["status"] == "completed" and res["stamped"] == 2, res
        for hid in (absurd, dead):
            assert _meta(hid)["verdict_pending"]["resolved_at"]

    def test_a_queued_resume_keeps_the_runs_project(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        from handle_queue import handle_task
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        # a landscape-bound run paused for an operator's answer
        rd = runs.create_run_dir("parenthd01", prompt=GOAL_FOLLOW_UP)
        runs.stamp_run_metadata_for("parenthd01", {"status": "interrupted", "pause_reason": "operator_question",
                                                   "project": "board-reports", "project_binding": "landscape"})
        seen = {}

        class _R:
            loop_id = "resumeloop1"
            status = "done"

        def _fake_loop(goal, **kwargs):
            seen.update(kwargs)
            return _R()

        task = {"job_id": "task-test-cont", "lane": "agenda", "source": "loop_continuation",
                "reason": "CONTINUATION of: finish the mission", "continuation_depth": 1,
                "origin": {"parent_handle_id": "parenthd01", "source": "operator_answer"}}
        with patch("agent_loop.run_agent_loop", side_effect=_fake_loop):
            handle_task(task, dry_run=True)
        assert seen.get("handle_id") == "parenthd01"
        assert seen.get("project") == "board-reports", seen.get("project")
        meta = _meta("parenthd01")
        assert (meta["project"], meta["project_binding"]) == ("board-reports", "landscape")
        # a legacy record with no project keeps today's derivation (None → loop init decides)
        runs.create_run_dir("parenthd02", prompt=GOAL_FOLLOW_UP)
        runs.stamp_run_metadata_for("parenthd02", {"status": "interrupted", "pause_reason": "budget_exhausted"})
        seen.clear()
        with patch("agent_loop.run_agent_loop", side_effect=_fake_loop):
            handle_task({**task, "origin": {"parent_handle_id": "parenthd02", "source": "task_store"}}, dry_run=True)
        assert seen.get("handle_id") == "parenthd02" and seen.get("project") is None


class TestTheFinalizeIsOneObligation:
    """Review round 9 (2026-09-13): a kept finalize write is drained whole;
    a handle whose kept write still fails is left out of the disk pass; a
    settlement the store already carries is not replayed over it; a
    marker-only finalize failure is kept too; the sweep's pid convention
    is the codebase's (`_pid_alive`): not ours = not the run's."""

    def _aged_transition(self, goal, answer, *, extra=None):
        active = {"kind": "escalation", "from": "board-reports", "from_binding": "landscape",
                  "to": "board-reports-escalated", "since": "2026-09-13T00:00:01+00:00"}
        return _finished_run(goal, answer, extra={"project": "board-reports-escalated", "project_binding": "escalated",
                                                  "project_transition": active, **(extra or {})}), active

    def test_a_failed_drain_does_not_fall_through_to_the_disk_revert(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import landscape
        import handle as handle_mod
        from audit_repair import sweep_transition_orphans
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        (projects_root() / "board-reports-escalated").mkdir(parents=True)
        hid, active = self._aged_transition(GOAL_QUARTERLY, "A.")
        adopted = {"project": "board-reports-escalated", "project_binding": "escalated",
                   "project_transition": {**active, "settled_at": "2026-09-13T00:20:00+00:00",
                                          "settled_by": "handle", "outcome": "adopted"}}
        monkeypatch.setattr(handle_mod, "_UNSETTLED_TRANSITIONS", {hid: dict(adopted)})
        real_for = runs.stamp_run_metadata_for
        real_revise = runs.revise_run_metadata_for
        writes = []
        refuse = {"n": 1}

        def recording(h, fields):
            writes.append((fields.get("project_transition") or {}).get("outcome"))
            return real_for(h, fields)

        def once_failing(h, fn):
            writes.append((fn(_meta(h)).get("project_transition") or {}).get("outcome"))
            if refuse["n"]:
                refuse["n"] -= 1
                return None  # the store could not be read or written
            return real_revise(h, fn)

        monkeypatch.setattr(runs, "stamp_run_metadata_for", recording)
        monkeypatch.setattr(runs, "revise_run_metadata_for", once_failing)
        res = sweep_transition_orphans(grace_s=60)
        assert res == {"status": "completed", "stamped": 0, "considered": 0, "retried": 0, "dropped": 0}, res
        assert writes == ["adopted"], writes  # no reverting write followed the failed drain
        meta = _meta(hid)
        assert meta["project"] == "board-reports-escalated" and "settled_at" not in meta["project_transition"]
        assert hid in handle_mod._UNSETTLED_TRANSITIONS
        res = sweep_transition_orphans(grace_s=60)
        assert res == {"status": "completed", "stamped": 0, "considered": 0, "retried": 1, "dropped": 0}, res
        assert writes == ["adopted", "adopted"]
        meta = _meta(hid)
        assert (meta["project"], meta["project_transition"]["outcome"]) == ("board-reports-escalated", "adopted")
        assert landscape.run_settled(meta) is True and hid not in handle_mod._UNSETTLED_TRANSITIONS

    def test_a_settlement_the_store_already_carries_is_not_replayed(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import landscape
        import handle as handle_mod
        from audit_repair import sweep_transition_orphans
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        # (a) another process's sweep reverted it first: the disk's settlement stands
        reverted, active = self._aged_transition(GOAL_QUARTERLY, "A.")
        runs.stamp_run_metadata_for(reverted, {
            "project": "board-reports", "project_binding": "landscape",
            "project_transition": {**active, "settled_at": "2026-09-13T01:05:00+00:00",
                                   "settled_by": "transition_orphan_sweep", "outcome": "reverted"}})
        adopted = {"project": "board-reports-escalated", "project_binding": "escalated",
                   "project_transition": {**active, "settled_at": "2026-09-13T00:20:00+00:00",
                                          "settled_by": "handle", "outcome": "adopted"}}
        # (b) the handle ran a LATER transition (RESUME reuses the id): the kept
        #     settlement is for another transition, but its marker resolution is still owed
        later, _ = self._aged_transition(GOAL_QUARTERLY + " again", "B.", extra={
            "verdict_pending": {"since": "2026-09-13T02:00:00+00:00", "loop_id": "lr-9"}})
        runs.stamp_run_metadata_for(later, {"project_transition": {**active, "since": "2026-09-13T02:00:01+00:00"}})
        monkeypatch.setattr(handle_mod, "_UNSETTLED_TRANSITIONS", {
            reverted: dict(adopted),
            later: {**adopted, "_finalize": True,
                    "verdict_pending": {"since": "2026-09-13T02:00:00+00:00", "loop_id": "lr-9",
                                        "resolved_at": "2026-09-13T02:30:00+00:00"}}})
        # the decision reads the store's LOCKED snapshot, not a read of its
        # own — an unreadable pre-read is not "write as kept" (review r10)
        import audit_repair
        monkeypatch.setattr(audit_repair, "_read_metadata", lambda rd: None)
        res = sweep_transition_orphans(grace_s=10 ** 9)
        assert res["retried"] == 1 and res["dropped"] == 1 and res["stamped"] == 0, res
        assert handle_mod._UNSETTLED_TRANSITIONS == {}
        m = _meta(reverted)
        assert (m["project"], m["project_transition"]["outcome"]) == ("board-reports", "reverted")
        assert m["project_transition"]["settled_by"] == "transition_orphan_sweep"
        m2 = _meta(later)
        assert m2["verdict_pending"]["resolved_at"] == "2026-09-13T02:30:00+00:00"
        assert m2["project"] == "board-reports-escalated" and "settled_at" not in m2["project_transition"]
        assert m2["project_transition"]["since"] == "2026-09-13T02:00:01+00:00"
        assert landscape.run_settled(m2) is False  # the later transition is the disk pass's, once aged

    def test_a_marker_only_finalize_failure_is_kept_and_drained(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import landscape
        import handle as handle_mod
        from audit_repair import sweep_transition_orphans
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        real_for = runs.revise_run_metadata_for
        refused = []

        def refusing_resolution(hid, fn):
            fields = fn(_meta(hid))
            vp = fields.get("verdict_pending")
            if isinstance(vp, dict) and vp.get("resolved_at"):
                refused.append(sorted(fields))
                return None
            return real_for(hid, fn)

        monkeypatch.setattr(runs, "revise_run_metadata_for", refusing_resolution)
        r, projects = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert projects == ["board-reports", "board-reports-escalated"]
        # the settlement itself was written in the run; the obligation also
        # records the story it may not get to tell (review r15)
        assert refused == [["story_owed_at", "story_owed_by", "verdict_pending"]], refused
        meta = _meta(r.handle_id)
        assert meta["project_transition"]["outcome"] == "adopted" and meta["project_transition"]["settled_at"]
        assert not meta["verdict_pending"].get("resolved_at") and landscape.run_settled(meta) is False
        kept = handle_mod._UNSETTLED_TRANSITIONS.get(r.handle_id)
        assert kept == {"_finalize": True, "_by": "owner"}, kept  # the obligation, not a materialised patch
        monkeypatch.setattr(runs, "revise_run_metadata_for", real_for)
        res = sweep_transition_orphans(grace_s=10 ** 9)
        assert res["retried"] == 1 and res["dropped"] == 0, res
        meta = _meta(r.handle_id)
        assert meta["verdict_pending"]["resolved_at"] and landscape.run_settled(meta) is True
        assert (meta["project"], meta["project_binding"]) == ("board-reports-escalated", "escalated")
        assert r.handle_id not in handle_mod._UNSETTLED_TRANSITIONS

    def test_a_pid_that_is_not_ours_is_not_the_runs_process(self, monkeypatch, tmp_path):
        """The convention pinned, with its premise: every worker on a
        workspace runs as the workspace's user (`audit_repair._pid_alive`
        reads EPERM the same way), so a pid we cannot signal is a system
        process that took the number after the run's died. The other
        reading leaves the run unresolved — and out of the landscape — for
        that process's life."""
        _setup(monkeypatch, tmp_path)
        import os
        import runs
        import notify
        import audit_repair
        from audit_repair import sweep_verdict_orphans
        monkeypatch.setattr(notify, "emit", lambda *a, **kw: True)
        marker = {"since": "2026-09-13T00:00:00+00:00", "loop_id": "lr-1", "notified_early": True}
        hid = _finished_run(GOAL_QUARTERLY, "A.", extra={"loop_ids": ["lr-1"], "verdict_pending": dict(marker),
                                                          "goal_verdict_source": "closure", "goal_achieved": True})
        runs.stamp_run_metadata_for(hid, {"pid": 1})
        real_kill = os.kill

        def eperm(pid, sig):
            if pid == 1 and sig == 0:
                raise PermissionError("[Errno 1] Operation not permitted")
            return real_kill(pid, sig)

        monkeypatch.setattr(os, "kill", eperm)
        assert audit_repair._pid_alive(1) is False
        res = sweep_verdict_orphans(grace_s=0)
        assert res["status"] == "completed" and res["stamped"] == 1, res
        assert _meta(hid)["verdict_pending"]["resolved_at"]


class TestTheObligationNeedsNoRead:
    """Review round 10 (2026-09-13): the finalize's obligation exists
    without a read of its own and the drain materialises the marker's
    resolution from the store's snapshot; a store that cannot be read
    defers the drain rather than writing as kept; the eligibility decision
    and the publication share one locked snapshot."""

    def test_a_finalize_that_could_not_read_still_owes_the_marker(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import landscape
        import handle as handle_mod
        from audit_repair import sweep_transition_orphans
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        real = runs.revise_run_metadata_for
        failed = []

        def unreadable(hid, fn):
            failed.append(hid)
            raise OSError("[Errno 5] Input/output error: metadata.json")

        monkeypatch.setattr(runs, "revise_run_metadata_for", unreadable)
        r, projects = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert projects == ["board-reports", "board-reports-escalated"] and failed == [r.handle_id]
        meta = _meta(r.handle_id)
        # the run's own settlement was written in the run; only the marker is open
        assert meta["project_transition"]["outcome"] == "adopted" and meta["project_transition"]["settled_at"]
        assert not meta["verdict_pending"].get("resolved_at") and landscape.run_settled(meta) is False
        # the obligation was kept with NOTHING read — no patch to carry
        assert handle_mod._UNSETTLED_TRANSITIONS.get(r.handle_id) == {"_finalize": True, "_by": "owner"}
        monkeypatch.setattr(runs, "revise_run_metadata_for", real)
        res = sweep_transition_orphans(grace_s=10 ** 9)
        assert res["retried"] == 1 and res["dropped"] == 0 and res["stamped"] == 0, res
        meta = _meta(r.handle_id)
        assert meta["verdict_pending"]["resolved_at"] and meta["verdict_pending"]["loop_id"]
        assert landscape.run_settled(meta) is True and r.handle_id not in handle_mod._UNSETTLED_TRANSITIONS
        cands, _, _ = landscape.candidates(GOAL_FOLLOW_UP + " and headcount")
        assert any(c["handle_id"] == r.handle_id and c["project"] == "board-reports-escalated" for c in cands)
        # nothing owed twice: a finalize obligation over a resolved marker is a no-op
        handle_mod._UNSETTLED_TRANSITIONS[r.handle_id] = {"_finalize": True}
        assert sweep_transition_orphans(grace_s=10 ** 9)["dropped"] == 1
        assert _meta(r.handle_id)["verdict_pending"] == meta["verdict_pending"]

    def test_an_unreadable_store_defers_the_drain(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import file_lock
        import handle as handle_mod
        from audit_repair import sweep_transition_orphans
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        active = {"kind": "escalation", "from": "board-reports", "from_binding": "landscape",
                  "to": "board-reports-escalated", "since": "2026-09-13T00:00:01+00:00"}
        hid = _finished_run(GOAL_QUARTERLY, "A.", extra={"project": "board-reports-escalated", "project_binding": "escalated",
                                                          "project_transition": active})
        adopted = {"project": "board-reports-escalated", "project_binding": "escalated",
                   "project_transition": {**active, "settled_at": "2026-09-13T00:20:00+00:00",
                                          "settled_by": "handle", "outcome": "adopted"}}
        monkeypatch.setattr(handle_mod, "_UNSETTLED_TRANSITIONS", {hid: dict(adopted)})
        real_rmw = file_lock.locked_rmw
        before = _meta(hid)

        def unreadable(path, fn, **kw):
            if path.name == "metadata.json":
                raise OSError("[Errno 5] Input/output error")
            return real_rmw(path, fn, **kw)

        monkeypatch.setattr(file_lock, "locked_rmw", unreadable)
        res = sweep_transition_orphans(grace_s=60)
        assert res == {"status": "completed", "stamped": 0, "considered": 0, "retried": 0, "dropped": 0}, res
        assert hid in handle_mod._UNSETTLED_TRANSITIONS and _meta(hid) == before
        monkeypatch.setattr(file_lock, "locked_rmw", real_rmw)
        res = sweep_transition_orphans(grace_s=60)
        assert res["retried"] == 1 and res["stamped"] == 0, res
        assert _meta(hid)["project_transition"]["outcome"] == "adopted"

    def test_the_decision_and_the_write_share_one_snapshot(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import json as _json
        import file_lock
        import runs
        import handle as handle_mod
        from audit_repair import sweep_transition_orphans
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        active = {"kind": "escalation", "from": "board-reports", "from_binding": "landscape",
                  "to": "board-reports-escalated", "since": "2026-09-13T00:00:01+00:00"}
        hid = _finished_run(GOAL_QUARTERLY, "A.", extra={"project": "board-reports-escalated", "project_binding": "escalated",
                                                          "project_transition": active})
        adopted = {"project": "board-reports-escalated", "project_binding": "escalated",
                   "project_transition": {**active, "settled_at": "2026-09-13T00:20:00+00:00",
                                          "settled_by": "handle", "outcome": "adopted"}}
        monkeypatch.setattr(handle_mod, "_UNSETTLED_TRANSITIONS", {hid: dict(adopted)})
        real_rmw = file_lock.locked_rmw
        reverted = {"project": "board-reports", "project_binding": "landscape",
                    "project_transition": {**active, "settled_at": "2026-09-13T01:05:00+00:00",
                                           "settled_by": "transition_orphan_sweep", "outcome": "reverted"}}

        def another_sweep_first(path, fn, **kw):
            # every read before this point saw the transition ACTIVE; the
            # revert lands just before the lock is taken
            if path.name == "metadata.json":
                m = _json.loads(path.read_text(encoding="utf-8"))
                m.update(reverted)
                path.write_text(_json.dumps(m), encoding="utf-8")
            return real_rmw(path, fn, **kw)

        monkeypatch.setattr(file_lock, "locked_rmw", another_sweep_first)
        res = sweep_transition_orphans(grace_s=10 ** 9)
        assert res["dropped"] == 1 and res["retried"] == 0 and res["stamped"] == 0, res
        m = _meta(hid)
        assert (m["project"], m["project_transition"]["outcome"]) == ("board-reports", "reverted")
        assert handle_mod._UNSETTLED_TRANSITIONS == {}


class TestRecoveryHasAConsumerInEveryProcess:
    """Review round 11 (2026-09-13): a finished handle is recoverable by
    its own record (`finalized_at`, the final close) whatever process
    hosts it; a long-lived host drains its kept writes at its next
    handle; a drained write refreshes the run's surfaces like the disk
    path does."""

    def test_the_final_close_records_that_the_finalize_ran(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        r, _ = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        meta = _meta(r.handle_id)
        assert meta["finalized_at"] and meta["ended_at"]
        assert meta["verdict_pending"]["resolved_at"] <= meta["finalized_at"]

    def test_a_finalized_run_with_an_active_marker_is_recovered_whatever_its_pid(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import os
        from datetime import datetime, timezone
        import runs
        import landscape
        import notify
        from audit_repair import sweep_verdict_orphans
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        (projects_root() / "board-reports-escalated").mkdir(parents=True)
        emitted = []
        monkeypatch.setattr(notify, "emit", lambda kind, *a, **kw: emitted.append(kind) or True)
        young = {"since": datetime.now(timezone.utc).isoformat(), "loop_id": "lr-1", "notified_early": True}
        active = {"kind": "escalation", "from": "board-reports", "from_binding": "landscape",
                  "to": "board-reports-escalated", "since": "2026-09-13T00:00:01+00:00"}
        # the handle finished (its finalize ran: the marker write failed, the
        # close landed) — the host is THIS live process, the marker is young
        finalized = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "loop_ids": ["lr-1"], "verdict_pending": dict(young), "goal_verdict_source": "closure",
            "goal_achieved": True, "finalized_at": datetime.now(timezone.utc).isoformat(),
            "final_notified_at": datetime.now(timezone.utc).isoformat(),
            "project": "board-reports-escalated", "project_binding": "escalated", "project_transition": active})
        # the same shape WITHOUT the final close: an early-closed run whose tail still runs
        early = _finished_run(GOAL_QUARTERLY + " again", "B.", extra={
            "loop_ids": ["lr-2"], "verdict_pending": {**young, "loop_id": "lr-2"},
            "goal_verdict_source": "closure", "goal_achieved": True})
        for hid in (finalized, early):
            runs.stamp_run_metadata_for(hid, {"pid": os.getpid()})
        res = sweep_verdict_orphans(grace_s=3600)
        assert res["status"] == "completed" and res["stamped"] == 1, res
        m = _meta(finalized)
        assert m["verdict_pending"]["resolved_at"] and m["goal_verdict_source"] == "closure"
        # the provisional retry project is not the record: reverted in the same write
        assert (m["project"], m["project_binding"]) == ("board-reports", "landscape")
        assert m["project_transition"]["outcome"] == "reverted" and landscape.run_settled(m) is True
        # its story was told by the finalize (`final_notified_at`) — the
        # sweep owes no notify; see TestFinalizationIsNotDelivery for the
        # finalized run whose story was NOT told
        assert emitted == [], emitted
        e = _meta(early)
        assert not e["verdict_pending"].get("resolved_at")

    def test_a_long_lived_host_drains_its_kept_writes_at_its_next_handle(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import landscape
        import handle as handle_mod
        from handle import handle
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        marker = {"since": "2026-09-13T00:00:00+00:00", "loop_id": "lr-1", "notified_early": True}
        # an earlier run of this process: its finalize could not write, the obligation is held here
        earlier = _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={
            "project": "board-reports", "loop_ids": ["lr-1"], "verdict_pending": dict(marker),
            "goal_verdict_source": "closure", "goal_achieved": True})
        monkeypatch.setattr(handle_mod, "_UNSETTLED_TRANSITIONS", {earlier: {"_finalize": True}})
        assert landscape.run_settled(_meta(earlier)) is False
        # a dry run is side-effect free: it does not drain
        with _no_hosted_free(), _classify_now():
            handle(GOAL_FOLLOW_UP, adapter=_NowAndJudge(_related(1, "carries it forward")), force_lane="now", dry_run=True)
        assert earlier in handle_mod._UNSETTLED_TRANSITIONS and landscape.run_settled(_meta(earlier)) is False
        # the next real handle in this process drains it FIRST — so the
        # landscape it reads is settled
        with _no_hosted_free(), _classify_now():
            r = handle(GOAL_FOLLOW_UP, adapter=_NowAndJudge(_related(1, "carries it forward")), force_lane="now", dry_run=False)
        assert r.status == "done"
        assert handle_mod._UNSETTLED_TRANSITIONS == {}
        m = _meta(earlier)
        assert m["verdict_pending"]["resolved_at"] and landscape.run_settled(m) is True

    def test_a_drained_write_refreshes_the_runs_surfaces(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import run_curation
        import loop_report
        import handle as handle_mod
        from audit_repair import sweep_transition_orphans, drain_kept_writes
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        marker = {"since": "2026-09-13T00:00:00+00:00", "loop_id": "lr-1", "notified_early": True}
        hid = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "project": "board-reports", "loop_ids": ["lr-1"], "verdict_pending": dict(marker),
            "goal_verdict_source": "closure", "goal_achieved": True})
        cards, reports = [], []
        monkeypatch.setattr(run_curation, "refresh_run_card_classification",
                            lambda h, run_dir=None, **kw: cards.append(h) or {"handle_id": h})
        monkeypatch.setattr(loop_report, "write_reports_for_run_dir", lambda rd, **kw: reports.append(rd.name))
        monkeypatch.setattr(handle_mod, "_UNSETTLED_TRANSITIONS", {hid: {"_finalize": True}})
        res = drain_kept_writes()
        assert res == {"status": "completed", "retried": 1, "dropped": 0}, res
        assert cards == [hid] and len(reports) == 1 and reports[0].startswith(hid)
        # a dropped write (nothing owed) refreshes nothing
        handle_mod._UNSETTLED_TRANSITIONS[hid] = {"_finalize": True}
        assert sweep_transition_orphans(grace_s=10 ** 9)["dropped"] == 1
        assert cards == [hid] and len(reports) == 1
        assert drain_kept_writes() == {"status": "completed", "retried": 0, "dropped": 0}


class TestFinalizationIsNotDelivery:
    """Review round 12 (2026-09-13): `finalized_at` is the final close,
    which precedes the finalize's notify — the sweep's notify is keyed on
    the finalize's own delivery record; a drained marker over an
    unverdicted run makes the honest call the close's tripwire waited
    on; a RESUME never manufactures a project from a malformed record."""

    def test_a_finalized_run_owes_its_notify_until_the_finalize_sent_it(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import os
        from datetime import datetime, timezone
        import runs
        import notify
        from audit_repair import sweep_verdict_orphans
        emitted = []
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: emitted.append((kind, payload.get("handle_id"))) or True)
        now = datetime.now(timezone.utc).isoformat()
        young = {"since": now, "loop_id": "lr-1", "notified_early": True}
        # the final close landed, the process died before the finalize's emit
        untold = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "loop_ids": ["lr-1"], "verdict_pending": dict(young), "goal_verdict_source": "closure",
            "goal_achieved": True, "finalized_at": now})
        # the finalize's emit ran; only its marker write had failed
        told = _finished_run(GOAL_QUARTERLY + " again", "B.", extra={
            "loop_ids": ["lr-2"], "verdict_pending": {**young, "loop_id": "lr-2"}, "goal_verdict_source": "closure",
            "goal_achieved": True, "finalized_at": now, "final_notified_at": now})
        for hid in (untold, told):
            runs.stamp_run_metadata_for(hid, {"pid": os.getpid()})
        res = sweep_verdict_orphans(grace_s=3600)
        assert res["status"] == "completed" and res["stamped"] == 2, res
        for hid in (untold, told):
            assert _meta(hid)["verdict_pending"]["resolved_at"]
        assert emitted == [("run_verdict", untold)], emitted

    def test_the_finalize_records_that_it_told_the_story(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import notify
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        emitted = []
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: emitted.append(kind) or True)
        r, _ = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        meta = _meta(r.handle_id)
        assert "run_completed" in emitted or "run_verdict" in emitted
        assert meta["final_notified_at"] >= meta["finalized_at"] > meta["verdict_pending"]["resolved_at"][:0]

    def test_a_drained_marker_without_a_verdict_is_recorded_unverdicted(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import landscape
        import memory_ledger
        import captains_log
        import handle as handle_mod
        from stop_verdicts import VERDICT_SOURCE_NEVER_STAMPED
        from audit_repair import drain_kept_writes
        marker = {"since": "2026-09-13T00:00:00+00:00", "loop_id": "lr-1", "notified_early": True}
        # an AGENDA run closure never judged; its finalize could not resolve the marker
        hid = _finished_run(GOAL_QUARTERLY, "A.", extra={"loop_ids": ["lr-1"], "verdict_pending": dict(marker)})
        runs.stamp_run_metadata_for(hid, {"lane": "agenda", "finalized_at": "2026-09-13T00:10:00+00:00"})
        stamps, events = [], []
        monkeypatch.setattr(memory_ledger, "stamp_outcome_verdict",
                            lambda lid, **kw: (stamps.append((lid, kw.get("goal_verdict_source"), kw.get("goal_achieved"))),
                                               memory_ledger.OutcomeVerdictStampResult("updated"))[1])
        monkeypatch.setattr(captains_log, "log_event", lambda kind, **kw: events.append((kind, kw.get("subject"))))
        monkeypatch.setattr(handle_mod, "_UNSETTLED_TRANSITIONS", {hid: {"_finalize": True}})
        # a ledger that cannot be stamped defers the whole obligation — the
        # marker stays active, the entry stays kept
        monkeypatch.setattr(memory_ledger, "stamp_outcome_verdict",
                            lambda lid, **kw: (_ for _ in ()).throw(OSError("ledger locked")))
        res = drain_kept_writes()
        assert res == {"status": "completed", "retried": 0, "dropped": 0}, res
        assert hid in handle_mod._UNSETTLED_TRANSITIONS and not _meta(hid)["verdict_pending"].get("resolved_at")
        # the ledger back: ledger row + event FIRST, then the marker
        monkeypatch.setattr(memory_ledger, "stamp_outcome_verdict",
                            lambda lid, **kw: (stamps.append((lid, kw.get("goal_verdict_source"), kw.get("goal_achieved"))),
                                               memory_ledger.OutcomeVerdictStampResult("updated"))[1])
        res = drain_kept_writes()
        assert res == {"status": "completed", "retried": 1, "dropped": 0}, res
        assert stamps == [("lr-1", VERDICT_SOURCE_NEVER_STAMPED, None)], stamps
        assert events and events[-1][0] == captains_log.DONE_WITHOUT_VERDICT and events[-1][1] == hid
        m = _meta(hid)
        assert m["verdict_pending"]["resolved_at"] and landscape.run_settled(m) is True
        # a judged run is not re-recorded
        judged = _finished_run(GOAL_QUARTERLY + " again", "B.", extra={
            "loop_ids": ["lr-2"], "verdict_pending": {**marker, "loop_id": "lr-2"},
            "goal_verdict_source": "closure", "goal_achieved": True})
        runs.stamp_run_metadata_for(judged, {"lane": "agenda"})
        handle_mod._UNSETTLED_TRANSITIONS[judged] = {"_finalize": True}
        stamps.clear()
        assert drain_kept_writes()["retried"] == 1 and stamps == []
        assert _meta(judged)["verdict_pending"]["resolved_at"]

    def test_a_resume_does_not_manufacture_a_project_from_a_malformed_record(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        from handle_queue import handle_task
        from orch_items import project_dir
        # review r20: padding is part of the operator-bound directory identity.
        assert project_dir(" board-reports ") != project_dir("board-reports")
        seen = {}

        class _R:
            loop_id = "resumeloop2"
            status = "done"

        def _fake_loop(goal, **kwargs):
            seen.update(kwargs)
            return _R()

        task = {"job_id": "task-test-cont2", "lane": "agenda", "source": "loop_continuation",
                "reason": "CONTINUATION of: finish the mission", "continuation_depth": 1,
                "origin": {"parent_handle_id": "", "source": "task_store"}}
        for n, (recorded, expected) in enumerate([(17, None), (True, None), (["board-reports"], None),
                                                   ("   ", None), (" board-reports ", " board-reports ")]):
            hid = f"parentbad{n}"
            runs.create_run_dir(hid, prompt=GOAL_FOLLOW_UP)
            runs.stamp_run_metadata_for(hid, {"status": "interrupted", "pause_reason": "budget_exhausted",
                                              "project": recorded, "project_binding": "operator"})
            seen.clear()
            with patch("agent_loop.run_agent_loop", side_effect=_fake_loop):
                handle_task({**task, "origin": {"parent_handle_id": hid, "source": "task_store"}}, dry_run=False)
            assert seen.get("handle_id") == hid
            assert seen.get("project") == expected, (recorded, seen.get("project"))


class TestTheStoryIsItsOwnObligation:
    """Review round 13 (2026-09-13): telling the run's story is an
    obligation independent of the verdict marker — a finalized run with
    no delivery record is told by its own sweep whatever the marker's
    state; delivery is the hook running cleanly or no hook being owed,
    never the attempt; the ledger's typed failure defers the drain; the
    heartbeat runs the untold sweep in its own scope."""

    def test_r20_only_repair_stories_bypass_a_live_owners_grace(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import os
        import runs
        import notify
        from audit_repair import reconcile_kept_write, sweep_untold_finalizes
        told = []
        monkeypatch.setattr(notify, "tell", lambda kind, payload, **kw:
                            told.append(payload["handle_id"]) or True)
        # review r20: an early close still lets the live owner change its final story.
        for by in ("owner", "repair", "legacy"):
            hid = _finished_run(GOAL_QUARTERLY + by, "A.", extra={
                "pid": os.getpid(), "verdict_pending": {"since": "2026-09-13T00:00:00+00:00"}})
            runs.revise_run_metadata_for(hid, lambda existing: reconcile_kept_write(
                existing, {"_finalize": True, "_by": "repair" if by == "legacy" else by}))
            m = _meta(hid)
            assert m["ended_at"] and m["story_owed_at"] and m["verdict_pending"]["resolved_at"]
            if by == "legacy":
                # A pre-r20 repair has no attribution field.
                m.pop("story_owed_by", None)
                (runs.run_dir(hid) / "metadata.json").write_text(json.dumps(m))
                assert "story_owed_by" not in _meta(hid)
            result = sweep_untold_finalizes(grace_s=3600)
            if by == "owner":
                assert result["told"] == 0
                assert told == []
                assert m["story_owed_by"] == "owner"
                runs.stamp_run_metadata_for(hid, {"pid": _dead_pid()})
                assert sweep_untold_finalizes(grace_s=3600)["told"] == 1
            else:
                assert result["told"] == 1
                if by == "repair":
                    assert m["story_owed_by"] == "repair"
            assert "_by" not in m and "_finalize" not in m
            assert told == [hid]
            assert sweep_untold_finalizes(grace_s=3600)["told"] == 0
            told.clear()

    def test_a_finalized_run_with_no_delivery_record_is_told_by_its_sweep(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import os
        from datetime import datetime, timezone, timedelta
        import runs
        import notify
        from audit_repair import sweep_untold_finalizes, sweep_verdict_orphans
        emitted = []
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: emitted.append((kind, payload.get("handle_id"))) or True)
        monkeypatch.setattr(notify, "hook_owed", lambda kind: False)
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=2)).isoformat()
        resolved = {"since": old, "loop_id": "lr-1", "notified_early": True, "resolved_at": old}
        # (a) the process died between the final close and the emit: marker RESOLVED, no record
        dead_untold = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "loop_ids": ["lr-1"], "verdict_pending": dict(resolved), "goal_verdict_source": "closure",
            "goal_achieved": True, "finalized_at": old})
        # (b) told already
        told = _finished_run(GOAL_QUARTERLY + " b", "B.", extra={
            "verdict_pending": dict(resolved), "finalized_at": old, "final_notified_at": old})
        # (c) a live finalize still in its curation (young, owner alive)
        young_alive = _finished_run(GOAL_QUARTERLY + " c", "C.", extra={
            "verdict_pending": dict(resolved), "finalized_at": now.isoformat()})
        # (d) aged with a live owner: the record failed after a clean emit — told again (accepted)
        aged_alive = _finished_run(GOAL_QUARTERLY + " d", "D.", extra={
            "verdict_pending": {**resolved, "notified_early": False}, "finalized_at": old})
        # (e) early-closed only (no final close): not this sweep's
        early_only = _finished_run(GOAL_QUARTERLY + " e", "E.", extra={"verdict_pending": dict(resolved)})
        runs.stamp_run_metadata_for(dead_untold, {"pid": _dead_pid()})
        for hid in (told, young_alive, aged_alive, early_only):
            runs.stamp_run_metadata_for(hid, {"pid": os.getpid()})
        res = sweep_untold_finalizes(grace_s=3600)
        assert res == {"status": "completed", "told": 2, "considered": 3}, res
        assert sorted(emitted) == sorted([("run_verdict", dead_untold), ("run_completed", aged_alive)]), emitted
        for hid in (dead_untold, aged_alive):
            m = _meta(hid)
            assert m["final_notified_at"] and m["final_notified_by"] == "untold_finalize_sweep"
        for hid in (young_alive, early_only):
            assert "final_notified_at" not in _meta(hid)
        assert _meta(told)["final_notified_at"] == old
        # nothing owed twice
        assert sweep_untold_finalizes(grace_s=3600)["told"] == 0 and len(emitted) == 2
        # the verdict sweep records what IT tells, the same way
        crash = _finished_run(GOAL_QUARTERLY + " f", "F.", extra={
            "loop_ids": ["lr-6"], "verdict_pending": {"since": old, "loop_id": "lr-6", "notified_early": True},
            "goal_verdict_source": "closure", "goal_achieved": True})
        runs.stamp_run_metadata_for(crash, {"pid": _dead_pid()})
        assert sweep_verdict_orphans(grace_s=0)["stamped"] == 1
        m = _meta(crash)
        assert m["verdict_pending"]["resolved_at"] and m["final_notified_by"] == "verdict_orphan_sweep"
        assert emitted[-1] == ("run_verdict", crash)

    def test_delivery_is_the_hook_running_cleanly_or_no_hook_owed(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import notify
        from audit_repair import sweep_untold_finalizes
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        # a CONFIGURED hook that fails: the attempt is not the story
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: False)
        monkeypatch.setattr(notify, "hook_owed", lambda kind: True)
        r, _ = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        meta = _meta(r.handle_id)
        assert meta["finalized_at"] and meta["verdict_pending"]["resolved_at"]
        assert "final_notified_at" not in meta
        # the sweep retries it — still failing, still owed
        runs.stamp_run_metadata_for(r.handle_id, {"pid": _dead_pid()})
        assert sweep_untold_finalizes(grace_s=0) == {"status": "completed", "told": 0, "considered": 1}
        assert "final_notified_at" not in _meta(r.handle_id)
        # the hook back: told and recorded
        emitted = []
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: emitted.append(kind) or True)
        assert sweep_untold_finalizes(grace_s=0)["told"] == 1 and emitted
        assert _meta(r.handle_id)["final_notified_by"] == "untold_finalize_sweep"
        # no hook at all: the journal row is the channel — told at the finalize
        _setup(monkeypatch, tmp_path / "two")
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: False)
        monkeypatch.setattr(notify, "hook_owed", lambda kind: False)
        r2, _ = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        assert _meta(r2.handle_id)["final_notified_at"]

    def test_the_ledgers_typed_failure_defers_the_drain(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import memory_ledger
        import captains_log
        import handle as handle_mod
        from memory_ledger import OutcomeVerdictStampResult
        from audit_repair import drain_kept_writes
        marker = {"since": "2026-09-13T00:00:00+00:00", "loop_id": "lr-1", "notified_early": True}
        hid = _finished_run(GOAL_QUARTERLY, "A.", extra={"loop_ids": ["lr-1"], "verdict_pending": dict(marker)})
        runs.stamp_run_metadata_for(hid, {"lane": "agenda", "finalized_at": "2026-09-13T00:10:00+00:00"})
        monkeypatch.setattr(captains_log, "log_event", lambda kind, **kw: None)
        monkeypatch.setattr(handle_mod, "_UNSETTLED_TRANSITIONS", {hid: {"_finalize": True}})
        results = {"status": "write_failed"}
        monkeypatch.setattr(memory_ledger, "stamp_outcome_verdict",
                            lambda lid, **kw: OutcomeVerdictStampResult(results["status"]))
        assert drain_kept_writes() == {"status": "completed", "retried": 0, "dropped": 0}
        assert hid in handle_mod._UNSETTLED_TRANSITIONS and not _meta(hid)["verdict_pending"].get("resolved_at")
        results["status"] = "invalid"
        assert drain_kept_writes()["retried"] == 0 and hid in handle_mod._UNSETTLED_TRANSITIONS
        results["status"] = "missing"  # a valid absence: no row to make honest
        assert drain_kept_writes()["retried"] == 1
        assert _meta(hid)["verdict_pending"]["resolved_at"]

    def test_the_heartbeat_runs_the_untold_sweep_in_its_own_scope(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import audit_repair
        import heartbeat as hb
        calls = []

        def boom(**kw):
            raise OverflowError("malformed record")

        monkeypatch.setattr(audit_repair, "sweep_verdict_orphans", boom)
        monkeypatch.setattr(audit_repair, "sweep_transition_orphans", boom)
        monkeypatch.setattr(audit_repair, "sweep_untold_finalizes",
                            lambda **kw: calls.append(kw) or {"status": "completed", "told": 2, "considered": 2})
        result = hb.stranded_state_sweep()
        assert calls and calls[0].get("limit") == 5
        assert result.get("untold_finalizes_told") == 2


class TestTheRecoveryTellsTheTrueStory:
    """Review round 14 (2026-09-13): the untold sweep rebuilds the card
    from the record before telling it (the final close precedes the
    curation, so a death between them leaves the answer-first card on
    disk); a crash-orphan the verdict sweep repaired carries its owed
    story in the resolution write, so a failed hook there — or a death
    after resolving — still reaches the untold sweep; an impossible pid
    is dead at the shared helper; `limit` bounds attempts, in a fair
    order."""

    def test_the_recovered_story_is_rebuilt_from_the_record_not_the_saved_card(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from datetime import datetime, timezone, timedelta
        import runs
        import notify
        import run_curation
        from audit_repair import sweep_untold_finalizes
        emitted = []
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: emitted.append((kind, dict(payload))) or True)
        monkeypatch.setattr(notify, "hook_owed", lambda kind: False)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        resolved = {"since": old, "loop_id": "lr-1", "notified_early": True, "resolved_at": old}
        # the process died between the final close and the curation: the
        # record carries the judged verdict, the saved card is the early one
        hid = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "loop_ids": ["lr-1"], "verdict_pending": dict(resolved), "goal_verdict_source": "closure",
            "goal_achieved": True, "finalized_at": old})
        runs.stamp_run_metadata_for(hid, {"pid": _dead_pid()})
        stale = {"handle_id": hid, "status": "done", "success_class": "done-verdict-pending",
                 "goal_achieved": None, "verdict_pending": True, "promotion": {"kept": "by maintenance"}}
        (runs.run_dir(hid) / "run_card.json").write_text(json.dumps(stale), encoding="utf-8")
        assert sweep_untold_finalizes(grace_s=3600)["told"] == 1
        kind, payload = emitted[-1]
        assert kind == "run_verdict" and payload["handle_id"] == hid
        assert payload["goal_achieved"] is True and payload["success_class"] != "done-verdict-pending", payload
        assert not payload.get("verdict_pending")
        saved = json.loads((runs.run_dir(hid) / "run_card.json").read_text(encoding="utf-8"))
        assert saved["goal_achieved"] is True and saved["promotion"] == {"kept": "by maintenance"}
        # the rebuild failing: the record's own verdict is the payload, never the stale card
        hid2 = _finished_run(GOAL_QUARTERLY + " b", "B.", extra={
            "verdict_pending": dict(resolved), "goal_verdict_source": "closure",
            "goal_achieved": False, "finalized_at": old})
        runs.stamp_run_metadata_for(hid2, {"pid": _dead_pid()})
        (runs.run_dir(hid2) / "run_card.json").write_text(json.dumps({**stale, "handle_id": hid2}), encoding="utf-8")

        def boom(*a, **kw):
            raise RuntimeError("curation unavailable")

        monkeypatch.setattr(run_curation, "refresh_run_card_classification", boom)
        assert sweep_untold_finalizes(grace_s=3600)["told"] == 1
        kind, payload = emitted[-1]
        assert payload["handle_id"] == hid2 and payload["goal_achieved"] is False
        assert payload["goal_verdict_source"] == "closure" and "success_class" not in payload
        assert _meta(hid2)["final_notified_by"] == "untold_finalize_sweep"

    def test_a_repaired_orphans_story_survives_its_hook_failing_or_the_sweep_dying(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from datetime import datetime, timezone, timedelta
        import runs
        import notify
        from audit_repair import sweep_untold_finalizes, sweep_verdict_orphans
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        active = {"since": old, "loop_id": "lr-1", "notified_early": True}
        # (a) verdict present, early-closed, never finalized, owner dead
        judged = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "loop_ids": ["lr-1"], "verdict_pending": dict(active), "goal_verdict_source": "closure",
            "goal_achieved": True})
        # (b) no verdict at all: the pending-orphaned branch
        unjudged = _finished_run(GOAL_QUARTERLY + " b", "B.", extra={
            "loop_ids": ["lr-2"], "verdict_pending": {**active, "loop_id": "lr-2"}})
        # (c) the sweep dies after resolving: the epilogue never runs
        dies = _finished_run(GOAL_QUARTERLY + " c", "C.", extra={
            "loop_ids": ["lr-3"], "verdict_pending": {**active, "loop_id": "lr-3"}, "goal_verdict_source": "closure",
            "goal_achieved": True})
        for hid in (judged, unjudged, dies):
            runs.stamp_run_metadata_for(hid, {"pid": _dead_pid()})
        attempts = []

        def failing(kind, payload, **kw):
            attempts.append((kind, payload.get("handle_id")))
            if payload.get("handle_id") == dies:
                raise RuntimeError("the process died here")
            return False

        monkeypatch.setattr(notify, "emit", failing)
        monkeypatch.setattr(notify, "hook_owed", lambda kind: True)
        assert sweep_verdict_orphans(grace_s=0)["stamped"] == 3
        for hid in (judged, unjudged, dies):
            m = _meta(hid)
            assert m["verdict_pending"]["resolved_at"] and "finalized_at" not in m
            assert m["story_owed_at"] and m["story_owed_by"] == "repair" and "final_notified_at" not in m, m
        assert len(attempts) == 3
        # the verdict sweep is done with them; the hook still failing keeps them owed
        assert sweep_verdict_orphans(grace_s=0)["stamped"] == 0
        assert sweep_untold_finalizes(grace_s=0) == {"status": "completed", "told": 0, "considered": 3}
        assert len(attempts) == 6
        # the hook back: told and recorded, once
        told = []
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: told.append((kind, payload.get("handle_id"))) or True)
        assert sweep_untold_finalizes(grace_s=0)["told"] == 3
        assert sorted(told) == sorted([("run_verdict", judged), ("run_verdict", unjudged), ("run_verdict", dies)]), told
        for hid in (judged, unjudged, dies):
            assert _meta(hid)["final_notified_by"] == "untold_finalize_sweep"
        assert sweep_untold_finalizes(grace_s=0)["told"] == 0 and len(told) == 3
        # a repair whose epilogue DID deliver owes nothing more
        fine = _finished_run(GOAL_QUARTERLY + " d", "D.", extra={
            "loop_ids": ["lr-4"], "verdict_pending": {**active, "loop_id": "lr-4"}, "goal_verdict_source": "closure",
            "goal_achieved": True})
        runs.stamp_run_metadata_for(fine, {"pid": _dead_pid()})
        assert sweep_verdict_orphans(grace_s=0)["stamped"] == 1
        m = _meta(fine)
        assert m["story_owed_at"] and m["final_notified_by"] == "verdict_orphan_sweep"
        assert sweep_untold_finalizes(grace_s=0)["told"] == 0

    def test_an_impossible_pid_does_not_abort_the_untold_sweep(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from datetime import datetime, timezone, timedelta
        import runs
        import notify
        from audit_repair import sweep_untold_finalizes, _pid_alive
        assert _pid_alive(2 ** 80) is False and _pid_alive(-1) is False
        emitted = []
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: emitted.append(payload.get("handle_id")) or True)
        monkeypatch.setattr(notify, "hook_owed", lambda kind: False)
        now = datetime.now(timezone.utc)
        resolved = {"since": "2026-09-13T00:00:00+00:00", "notified_early": False, "resolved_at": "x"}
        # both young under a 4 h grace: the impossible pid sorts first (older)
        absurd = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "verdict_pending": dict(resolved), "finalized_at": (now - timedelta(hours=3)).isoformat()})
        healthy = _finished_run(GOAL_QUARTERLY + " b", "B.", extra={
            "verdict_pending": dict(resolved), "finalized_at": (now - timedelta(hours=2)).isoformat()})
        runs.stamp_run_metadata_for(absurd, {"pid": 2 ** 80})
        runs.stamp_run_metadata_for(healthy, {"pid": _dead_pid()})
        res = sweep_untold_finalizes(grace_s=4 * 3600)
        assert res == {"status": "completed", "told": 2, "considered": 2}, res
        assert emitted == [absurd, healthy], emitted

    def test_the_limit_bounds_attempts_and_a_failing_row_does_not_shadow_the_rest(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from datetime import datetime, timezone, timedelta
        import runs
        import notify
        from audit_repair import sweep_untold_finalizes
        attempts = []
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: attempts.append(payload.get("handle_id")) or False)
        monkeypatch.setattr(notify, "hook_owed", lambda kind: True)
        base = datetime.now(timezone.utc) - timedelta(hours=3)
        resolved = {"since": "2026-09-13T00:00:00+00:00", "notified_early": False, "resolved_at": "x"}
        ids = []
        for i in range(12):
            ids.append(_finished_run(f"{GOAL_QUARTERLY} {i}", "A.", extra={
                "verdict_pending": dict(resolved), "finalized_at": (base + timedelta(minutes=i)).isoformat()}))
        res = sweep_untold_finalizes(grace_s=0, limit=5)
        assert res == {"status": "completed", "told": 0, "considered": 5}, res
        assert attempts == ids[:5], (attempts, ids)
        for hid in ids[:5]:
            assert _meta(hid)["final_notify_attempted_at"] and "final_notified_at" not in _meta(hid)
        # the next tick reaches the rows behind the failing ones
        attempts.clear()
        assert sweep_untold_finalizes(grace_s=0, limit=5)["considered"] == 5
        assert attempts == ids[5:10], attempts
        attempts.clear()
        assert sweep_untold_finalizes(grace_s=0, limit=5)["considered"] == 5
        assert attempts == ids[10:] + ids[:3], attempts
        # the hook back: everything owed is told
        told = []
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: told.append(payload.get("handle_id")) or True)
        assert sweep_untold_finalizes(grace_s=0, limit=20)["told"] == 12
        assert sorted(told) == sorted(ids)
        assert sweep_untold_finalizes(grace_s=0)["told"] == 0


class TestTheStoryIsAcknowledgedByItsChannel:
    """Review round 15 (2026-09-13): a story is told only when its OWED
    channel says so (`notify.tell`: the hook when one is configured for
    the event, else the journal row); the drain's finalize obligation
    records the owed story; the verdict sweep's fallback payload is the
    record, never an id; a still-pending verdict is the verdict sweep's
    to tell, never acknowledged early by the untold sweep."""

    def test_a_drained_finalize_leaves_its_story_owed(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import notify
        import handle as handle_mod
        from audit_repair import drain_kept_writes, sweep_untold_finalizes, sweep_verdict_orphans
        marker = {"since": "2026-09-13T00:00:00+00:00", "loop_id": "lr-1", "notified_early": True}
        # the finalize's resolving write, its close and its emit all failed:
        # the early close is the record; the host keeps the obligation
        owed = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "loop_ids": ["lr-1"], "verdict_pending": dict(marker), "goal_verdict_source": "closure",
            "goal_achieved": True})
        # the same, but the emit DID deliver before the host drained
        told_already = _finished_run(GOAL_QUARTERLY + " b", "B.", extra={
            "loop_ids": ["lr-2"], "verdict_pending": {**marker, "loop_id": "lr-2"}, "goal_verdict_source": "closure",
            "goal_achieved": True, "final_notified_at": "2026-09-13T00:05:00+00:00"})
        for hid in (owed, told_already):
            runs.stamp_run_metadata_for(hid, {"pid": _dead_pid()})
        monkeypatch.setattr(handle_mod, "_UNSETTLED_TRANSITIONS", {owed: {"_finalize": True, "_by": "owner"},
                                                                    told_already: {"_finalize": True}})
        assert drain_kept_writes()["retried"] == 2
        m = _meta(owed)
        assert m["verdict_pending"]["resolved_at"] and "finalized_at" not in m
        assert m["story_owed_at"] and m["story_owed_by"] == "repair" and "final_notified_at" not in m
        assert "story_owed_at" not in _meta(told_already)
        # the marker is resolved: not the verdict sweep's; the story is the untold sweep's
        assert sweep_verdict_orphans(grace_s=0)["stamped"] == 0
        told = []
        monkeypatch.setattr(notify, "tell", lambda kind, payload, **kw: told.append((kind, payload.get("handle_id"), payload.get("goal_achieved"))) or True)
        assert sweep_untold_finalizes(grace_s=0) == {"status": "completed", "told": 1, "considered": 1}
        assert told == [("run_verdict", owed, True)], told
        assert _meta(owed)["final_notified_by"] == "untold_finalize_sweep"
        assert sweep_untold_finalizes(grace_s=0)["told"] == 0

    def test_the_verdict_sweeps_fallback_payload_is_the_record(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        import notify
        import run_curation
        from stop_verdicts import VERDICT_SOURCE_PENDING_ORPHANED
        from audit_repair import sweep_untold_finalizes, sweep_verdict_orphans
        marker = {"since": "2026-09-13T00:00:00+00:00", "loop_id": "lr-1", "notified_early": True}
        judged = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "loop_ids": ["lr-1"], "verdict_pending": dict(marker), "goal_verdict_source": "closure",
            "goal_achieved": False})
        unjudged = _finished_run(GOAL_QUARTERLY + " b", "B.", extra={
            "loop_ids": ["lr-2"], "verdict_pending": {**marker, "loop_id": "lr-2"}})
        for hid in (judged, unjudged):
            runs.stamp_run_metadata_for(hid, {"pid": _dead_pid()})

        def boom(*a, **kw):
            raise OSError("curation unavailable")

        monkeypatch.setattr(run_curation, "refresh_run_card_classification", boom)
        told = {}
        monkeypatch.setattr(notify, "tell", lambda kind, payload, **kw: told.__setitem__(payload["handle_id"], (kind, dict(payload))) or True)
        assert sweep_verdict_orphans(grace_s=0)["stamped"] == 2
        kind, p = told[judged]
        assert kind == "run_verdict" and p["goal_achieved"] is False and p["goal_verdict_source"] == "closure"
        assert p["status"] == "done" and p["goal"] == GOAL_QUARTERLY and "success_class" not in p
        kind, p = told[unjudged]
        assert p["goal_achieved"] is None and p["goal_verdict_source"] == VERDICT_SOURCE_PENDING_ORPHANED, p
        for hid in (judged, unjudged):
            m = _meta(hid)
            assert m["final_notified_by"] == "verdict_orphan_sweep" and m["story_owed_at"]
        assert sweep_untold_finalizes(grace_s=0)["told"] == 0

    def test_no_hook_means_the_journal_row_is_the_acknowledgment(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from datetime import datetime, timezone, timedelta
        import runs
        import notify
        import observe
        from audit_repair import sweep_untold_finalizes
        # the unit: the owed channel's word, nothing else
        monkeypatch.setattr(observe, "write_event", lambda *a, **kw: False)
        assert notify.tell("run_completed", {"handle_id": "x", "status": "done"}) is False
        monkeypatch.setattr(observe, "write_event", lambda *a, **kw: True)
        assert notify.tell("run_completed", {"handle_id": "x", "status": "done"}) is True
        monkeypatch.setattr(notify, "hook_owed", lambda kind: True)
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: False)
        assert notify.tell("run_completed", {"handle_id": "x"}) is False  # the hook is owed, and failed
        monkeypatch.setattr(observe, "write_event", lambda *a, **kw: False)
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: True)
        assert notify.tell("run_completed", {"handle_id": "x"}) is True  # the hook is owed, and delivered
        monkeypatch.setattr(notify, "hook_owed", lambda kind: False)

        def torn(*a, **kw):
            raise OSError("journal unwritable")

        monkeypatch.setattr(observe, "write_event", torn)
        assert notify.tell("run_completed", {"handle_id": "x"}) is False
        # the sweep with no hook and a failing journal: owed, then told when the row lands
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        hid = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "verdict_pending": {"since": old, "notified_early": False, "resolved_at": old},
            "finalized_at": old, "goal_verdict_source": "closure", "goal_achieved": True})
        runs.stamp_run_metadata_for(hid, {"pid": _dead_pid()})
        assert sweep_untold_finalizes(grace_s=0) == {"status": "completed", "told": 0, "considered": 1}
        m = _meta(hid)
        assert m["final_notify_attempted_at"] and "final_notified_at" not in m
        rows = []
        monkeypatch.setattr(observe, "write_event", lambda kind, **kw: rows.append((kind, kw)) or True)
        assert sweep_untold_finalizes(grace_s=0)["told"] == 1
        assert rows and rows[-1][0] == "run_completed"
        assert _meta(hid)["final_notified_by"] == "untold_finalize_sweep"

    def test_a_still_pending_verdict_is_not_acknowledged_as_the_story(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from datetime import datetime, timezone, timedelta
        import runs
        import notify
        import memory_ledger
        from memory_ledger import OutcomeVerdictStampResult
        from stop_verdicts import VERDICT_SOURCE_PENDING_ORPHANED
        from audit_repair import sweep_untold_finalizes, sweep_verdict_orphans
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        # finalized, unjudged, marker still ACTIVE (the resolving write failed), owner dead
        hid = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "loop_ids": ["lr-1"], "verdict_pending": {"since": old, "loop_id": "lr-1", "notified_early": True},
            "finalized_at": old})
        runs.stamp_run_metadata_for(hid, {"pid": _dead_pid()})
        results = {"status": "write_failed"}
        monkeypatch.setattr(memory_ledger, "stamp_outcome_verdict",
                            lambda lid, **kw: OutcomeVerdictStampResult(results["status"]))
        told = []
        monkeypatch.setattr(notify, "tell", lambda kind, payload, **kw: told.append((kind, dict(payload))) or True)
        # the verdict repair is blocked: nobody tells a pending story as the final one
        assert sweep_verdict_orphans(grace_s=0)["stamped"] == 0
        assert sweep_untold_finalizes(grace_s=0) == {"status": "completed", "told": 0, "considered": 0}
        assert told == [] and "final_notified_at" not in _meta(hid)
        # the ledger back: the verdict sweep resolves AND tells, once, with the resolved card
        results["status"] = "updated"
        assert sweep_verdict_orphans(grace_s=0)["stamped"] == 1
        assert len(told) == 1 and told[0][0] == "run_verdict"
        p = told[0][1]
        assert p["handle_id"] == hid and p["goal_verdict_source"] == VERDICT_SOURCE_PENDING_ORPHANED
        assert p.get("success_class") != "done-verdict-pending" and not p.get("verdict_pending"), p
        m = _meta(hid)
        assert m["verdict_pending"]["resolved_at"] and m["final_notified_by"] == "verdict_orphan_sweep"
        assert sweep_untold_finalizes(grace_s=0)["told"] == 0 and len(told) == 1


class TestEverySenderKeepsTheSameWord:
    """Review round 16 (2026-09-13): the lifecycle rules hold at EVERY
    sender — the finalize does not acknowledge a story told over a kept
    resolution (the resolver tells the verdict); its fallback payload is
    the record; the early answer records its owed channel's word and the
    routers read it; the journal row carries a bare story's verdict; an
    unknowable channel acknowledges nothing."""

    def test_the_finalize_does_not_acknowledge_a_story_told_over_a_kept_resolution(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import os
        import runs
        import notify
        import handle as handle_mod
        from audit_repair import drain_kept_writes, sweep_untold_finalizes, sweep_verdict_orphans
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        real_for = runs.revise_run_metadata_for

        def refusing_resolution(hid, fn):
            fields = fn(_meta(hid))
            vp = fields.get("verdict_pending")
            if isinstance(vp, dict) and vp.get("resolved_at"):
                return None
            return real_for(hid, fn)

        monkeypatch.setattr(runs, "revise_run_metadata_for", refusing_resolution)
        told = []
        monkeypatch.setattr(notify, "tell", lambda kind, payload, **kw: told.append((kind, dict(payload))) or True)
        r, _ = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        meta = _meta(r.handle_id)
        assert meta["finalized_at"] and not meta["verdict_pending"].get("resolved_at")
        assert r.handle_id in handle_mod._UNSETTLED_TRANSITIONS
        # the channel acknowledged the PENDING story; that is not the final one
        assert told and "final_notified_at" not in meta, meta.get("final_notified_at")
        n0 = len(told)
        # (a) this process drains: the resolution carries the owed story, and the
        #     untold sweep tells it now — the owner is alive, but its finalize is over
        runs.stamp_run_metadata_for(r.handle_id, {"pid": os.getpid()})
        monkeypatch.setattr(runs, "revise_run_metadata_for", real_for)
        assert drain_kept_writes()["retried"] == 1
        m = _meta(r.handle_id)
        assert m["verdict_pending"]["resolved_at"] and m["story_owed_at"] and "final_notified_at" not in m
        assert sweep_untold_finalizes(grace_s=3600) == {"status": "completed", "told": 1, "considered": 1}
        assert len(told) == n0 + 1 and told[-1][1]["handle_id"] == r.handle_id
        assert told[-1][1].get("success_class") != "done-verdict-pending" and not told[-1][1].get("verdict_pending")
        assert _meta(r.handle_id)["final_notified_by"] == "untold_finalize_sweep"
        assert sweep_verdict_orphans(grace_s=0)["stamped"] == 0 and sweep_untold_finalizes(grace_s=0)["told"] == 0
        # (b) another process's verdict sweep resolves first: it tells and records
        _setup(monkeypatch, tmp_path / "two")
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
        monkeypatch.setattr(runs, "revise_run_metadata_for", refusing_resolution)
        told.clear()
        r2, _ = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        monkeypatch.setattr(runs, "revise_run_metadata_for", real_for)
        runs.stamp_run_metadata_for(r2.handle_id, {"pid": os.getpid()})
        assert "final_notified_at" not in _meta(r2.handle_id)
        n1 = len(told)
        assert sweep_verdict_orphans(grace_s=10 ** 9)["stamped"] == 1
        m2 = _meta(r2.handle_id)
        assert m2["verdict_pending"]["resolved_at"] and m2["final_notified_by"] == "verdict_orphan_sweep"
        assert len(told) == n1 + 1 and told[-1][1].get("success_class") != "done-verdict-pending"
        handle_mod._UNSETTLED_TRANSITIONS.pop(r2.handle_id, None)

    def test_the_finalizes_fallback_payload_is_the_record(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import notify
        import run_curation
        from orch_items import projects_root
        (projects_root() / "board-reports").mkdir(parents=True)
        _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})

        def boom(*a, **kw):
            raise OSError("curation unavailable")

        monkeypatch.setattr(run_curation, "curate_run", boom)
        monkeypatch.setattr(run_curation, "refresh_run_card_classification", boom)
        told = []
        monkeypatch.setattr(notify, "tell", lambda kind, payload, **kw: told.append((kind, dict(payload))) or True)
        r, _ = _escalating_run(monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward")))
        meta = _meta(r.handle_id)
        assert meta["finalized_at"] and meta["verdict_pending"]["resolved_at"]
        kind, p = told[-1]
        assert p["handle_id"] == r.handle_id and p["status"] == meta["status"]
        assert "goal_achieved" in p and "goal_verdict_source" in p and p["goal"], p
        assert p["goal_achieved"] == meta.get("goal_achieved") and p["goal_verdict_source"] == meta.get("goal_verdict_source")
        assert meta["final_notified_at"]

    def test_an_unknowable_channel_acknowledges_nothing(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import config as config_mod
        import notify
        import observe
        monkeypatch.setattr(observe, "write_event", lambda *a, **kw: True)
        cfg = {}

        def snapshot(**kwargs):
            command = cfg.get("notify.command")
            if isinstance(command, Exception):
                raise command
            return {"notify": {key.removeprefix("notify."): value
                               for key, value in cfg.items()}}, []

        monkeypatch.setattr(config_mod, "snapshot", snapshot)
        payload = {"handle_id": "x", "status": "done"}
        cfg["notify.command"] = OSError("config unreadable")
        assert notify.hook_owed("run_completed") is None and notify.hook_configured("run_completed") is False
        assert notify.tell("run_completed", payload) is False
        cfg["notify.command"] = "some-hook"
        cfg["notify.events"] = 17
        assert notify.hook_owed("run_completed") is None
        assert notify.tell("run_completed", payload) is False
        cfg["notify.events"] = "run_completed"
        assert notify.hook_owed("run_completed") is None
        cfg["notify.events"] = ["run_completed"]
        assert notify.hook_owed("run_completed") is True and notify.hook_owed("run_verdict") is False
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: True)
        assert notify.tell("run_completed", payload) is True
        monkeypatch.setattr(notify, "emit", lambda kind, payload, **kw: False)
        assert notify.tell("run_completed", payload) is False
        cfg["notify.command"] = ""
        assert notify.hook_owed("run_completed") is False
        assert notify.tell("run_completed", payload) is True  # the journal's word
        monkeypatch.setattr(observe, "write_event", lambda *a, **kw: False)
        assert notify.tell("run_completed", payload) is False

    @pytest.mark.parametrize("fault", ["malformed", "unreadable", "non_mapping"])
    def test_real_config_fault_keeps_the_story_owed(self, monkeypatch, tmp_path, fault):
        _setup(monkeypatch, tmp_path)
        import config
        import notify
        import observe
        from pathlib import Path

        path = config._workspace_config_path()
        path.write_text("notify: [" if fault == "malformed" else
                        "- not a mapping" if fault == "non_mapping" else
                        "notify:\n  command: some-hook\n")
        real_read = Path.read_text
        blocked = fault == "unreadable"

        def read_text(self, *args, **kwargs):
            if self == path and blocked:
                raise OSError("config unreadable")
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)
        monkeypatch.setattr(observe, "write_event", lambda *a, **kw: True)
        payload = {"handle_id": "faulted", "status": "done"}
        assert config.get("notify.command", "") == ""
        assert notify.hook_owed("run_completed") is None
        assert config.load_faults() == [str(path)]
        assert notify.tell("run_completed", payload) is False

        # Recovery must be re-read even if the mtime has not changed.
        import os
        stat = path.stat()
        blocked = False
        path.write_text("notify: {}\n")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert config.get("notify.command", "") == ""
        assert config.load_faults() == []
        assert notify.hook_owed("run_completed") is False
        assert notify.tell("run_completed", payload) is True

        # an EMPTY file is an empty mapping, not a fault (the live default)
        path.write_text("")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
        assert config.get("notify.command", "") == ""
        assert config.load_faults() == []
        assert notify.hook_owed("run_completed") is False
        path.unlink()
        assert config.get("notify.command", "") == ""
        assert config.load_faults() == []
        assert notify.hook_owed("run_completed") is False
        assert notify.tell("run_completed", payload) is True

    def test_the_journal_row_carries_the_bare_story(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import notify
        import observe
        rows = []
        monkeypatch.setattr(observe, "write_event", lambda kind, **kw: rows.append((kind, kw)) or True)
        bare = {"handle_id": "abc12345", "status": "done", "goal": "Deliver the report",
                "goal_achieved": False, "goal_verdict_source": "closure"}
        assert notify.tell("run_completed", bare) is True
        assert rows[-1][0] == "run_completed" and rows[-1][1]["status"] == "done"
        assert rows[-1][1]["detail"] == "[abc12345] goal_achieved=False source=closure", rows[-1]
        card = {**bare, "result_excerpt": "Revenue rose 12%."}
        notify.tell("run_completed", card)
        failure = rows[-1][1]["detail"]
        assert failure == "[abc12345] goal_achieved=False source=closure; Revenue rose 12%."
        assert notify.tell("run_completed", {**card, "goal_achieved": True})
        success = rows[-1][1]["detail"]
        assert success == "[abc12345] goal_achieved=True source=closure; Revenue rose 12%."
        assert success != failure
        assert notify.tell("run_completed", {**card, "goal_achieved": None,
                                             "verdict_pending": True})
        assert rows[-1][1]["detail"] == (
            "[abc12345] goal_achieved=None source=closure verdict_pending; Revenue rose 12%.")
        assert notify.tell("run_completed", {"result_excerpt": "Revenue rose 12%."})
        assert rows[-1][1]["detail"] == "Revenue rose 12%."
        from context_budget import clip
        prefix = "[abc12345] goal_achieved=False source=closure; "
        assert notify.tell("run_completed", {**card, "result_excerpt": "x" * 500})
        assert rows[-1][1]["detail"] == prefix + clip("x" * 500, 300 - len(prefix))
        notify.tell("run_verdict", bare)
        assert rows[-1][1]["detail"].startswith("[abc12345] goal_achieved=False source=closure")

    def test_a_failed_early_journal_is_not_an_answer_that_reached(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from datetime import datetime, timezone, timedelta
        import runs
        import notify
        from audit_repair import sweep_untold_finalizes
        early = notify.early_reached
        assert early({"notified_early": True, "early_told": False, "hook_configured": False, "hook_delivered": False}) is False
        assert early({"notified_early": True, "early_told": True, "hook_configured": True, "hook_delivered": True}) is True
        # markers from before the word: reached unless a configured hook failed
        assert early({"notified_early": True, "hook_configured": True, "hook_delivered": False}) is False
        assert early({"notified_early": True, "hook_configured": False, "hook_delivered": False}) is True
        assert early({"notified_early": False, "early_told": True}) is False and early(None) is False
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        hid = _finished_run(GOAL_QUARTERLY, "A.", extra={
            "verdict_pending": {"since": old, "notified_early": True, "early_told": False, "resolved_at": old},
            "finalized_at": old, "goal_verdict_source": "closure", "goal_achieved": True})
        runs.stamp_run_metadata_for(hid, {"pid": _dead_pid()})
        told = []
        monkeypatch.setattr(notify, "tell", lambda kind, payload, **kw: told.append(kind) or True)
        assert sweep_untold_finalizes(grace_s=0)["told"] == 1
        assert told == ["run_completed"], told  # the full answer, not a verdict for an answer never received


def test_r21_finalize_keeps_obligation_private_until_close_and_tell(monkeypatch, tmp_path):
    import threading
    import runs
    import notify
    import observe
    import audit_repair
    import handle as handle_mod
    from orch_items import projects_root
    _setup(monkeypatch, tmp_path)
    (projects_root() / "board-reports").mkdir(parents=True)
    _finished_run(GOAL_QUARTERLY, "Revenue rose.", extra={"project": "board-reports"})
    monkeypatch.setattr(handle_mod, "_UNSETTLED_TRANSITIONS", {})
    monkeypatch.setattr(observe, "write_event", lambda *a, **kw: True)
    told = []
    monkeypatch.setattr(notify, "tell", lambda kind, payload, **kw: told.append((kind, payload)) or True)
    real_revise = runs.revise_run_metadata_for
    real_close = runs.close_run
    owner_in_close = threading.Event()
    release = threading.Event()
    ids = []
    results = []

    def refusing_finalize(hid, fn):
        if threading.current_thread() is owner:
            return None
        return real_revise(hid, fn)

    def blocking_close(hid, **kwargs):
        if kwargs.get("final"):
            ids.append(hid)
            owner_in_close.set()
            assert release.wait(10)
        return real_close(hid, **kwargs)

    def run_owner():
        results.append(_escalating_run(
            monkeypatch, GOAL_FOLLOW_UP, _NowAndJudge(_related(1, "carries it forward"))))

    monkeypatch.setattr(runs, "revise_run_metadata_for", refusing_finalize)
    monkeypatch.setattr(runs, "close_run", blocking_close)
    owner = threading.Thread(target=run_owner, daemon=True)
    owner.start()
    try:
        assert owner_in_close.wait(10)
        hid = ids[0]
        assert handle_mod._UNSETTLED_TRANSITIONS.get(hid) is None
        assert audit_repair.drain_kept_writes()["retried"] == 0
        assert "story_owed_at" not in _meta(hid)
    finally:
        release.set()
        owner.join(10)
    assert not owner.is_alive()
    assert results and told
    assert handle_mod._UNSETTLED_TRANSITIONS.get(hid) == {"_finalize": True, "_by": "owner"}
    assert audit_repair.drain_kept_writes()["retried"] == 1
    assert _meta(hid)["story_owed_by"] == "repair"
    assert _meta(hid)["verdict_pending"]["resolved_at"]
    assert hid not in handle_mod._UNSETTLED_TRANSITIONS
