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
        assert handle_mod._free_project_name("client-a", ("client-a",)) == "client-a-3"
        assert (root / "client-a-3").is_dir(), "the name is reserved, not merely observed"
        # the range exhausted: a random suffix, never the base
        monkeypatch.setattr(handle_mod, "_PROJECT_SIBLING_CAP", 4)
        (root / "client-a-4").mkdir()
        name = handle_mod._free_project_name("client-a", ("client-a",))
        assert name != "client-a" and name.startswith("client-a-") and (root / name).is_dir()
        assert len(name) == len("client-a-") + 8
        # even that taken: fail closed
        import uuid
        monkeypatch.setattr(uuid, "uuid4", lambda: type("U", (), {"hex": "deadbeefcafe"})())
        (root / "client-a-deadbeef").mkdir()
        with pytest.raises(RuntimeError):
            handle_mod._free_project_name("client-a", ("client-a",))
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
        first = handle_mod._free_project_name("client-a", ("client-a",))
        second = handle_mod._free_project_name("client-a", ("client-a",))
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
                handle_mod._free_project_name(bad, ())
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
