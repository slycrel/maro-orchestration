"""Chunk A pins — scored retrieval + camera-frame logging.

Two invariants:
  1. Scored variants are pure instrumentation: every plain ranker /
     query_lessons call returns byte-identical results to
     strip(scored variant). The recall loop-slice rewire rides on this —
     selection semantics must not move.
  2. The camera is harmless: killswitch respected (including stringly
     "false"), no run dir = no orphan writes, garbage input never raises,
     and a real loop-slice recall drops one joinable frame per call.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))


class _Doc:
    def __init__(self, text, created_at=""):
        self.lesson = text
        self.created_at = created_at


class TestScoredRankerEquivalence:
    QUERY = "socket retry backoff strategy"
    TEXTS = [
        "exponential backoff for socket retries works",
        "rsync beats scp for large trees",
        "disk pressure needs monitoring hooks",
        "retry budgets bound socket storms",
        "tls handshakes fail on clock skew",
    ]

    def test_bm25_plain_equals_scored_stripped(self):
        from hybrid_search import bm25_rank, bm25_rank_scored
        for top_k in (None, 2, 50):
            plain = bm25_rank(self.QUERY, self.TEXTS, top_k=top_k)
            scored = bm25_rank_scored(self.QUERY, self.TEXTS, top_k=top_k)
            assert plain == [d for d, _ in scored]
            assert all(isinstance(s, float) for _, s in scored)

    def test_bm25_no_signal_path_input_order_unscored(self):
        # "the of" tokenizes to nothing — bm25_rank returns docs as-is
        # (top_k ignored); the scored variant must mirror that exactly.
        from hybrid_search import bm25_rank, bm25_rank_scored
        plain = bm25_rank("the of", self.TEXTS, top_k=2)
        scored = bm25_rank_scored("the of", self.TEXTS, top_k=2)
        assert plain == self.TEXTS
        assert scored == [(t, 0.0) for t in self.TEXTS]

    def test_hybrid_plain_equals_scored_stripped_both_paths(self):
        from hybrid_search import hybrid_rank, hybrid_rank_scored
        # Pure-BM25 path (strings — no created_at)
        for top_k in (None, 3):
            plain = hybrid_rank(self.QUERY, self.TEXTS, top_k=top_k)
            scored = hybrid_rank_scored(self.QUERY, self.TEXTS, top_k=top_k)
            assert plain == [d for d, _ in scored]
        # RRF path (objects with created_at → recency fusion)
        docs = [_Doc(t, created_at=f"2026-07-{10 + i}T00:00:00")
                for i, t in enumerate(self.TEXTS)]
        for top_k in (None, 3):
            plain = hybrid_rank(self.QUERY, docs, top_k=top_k)
            scored = hybrid_rank_scored(self.QUERY, docs, top_k=top_k)
            assert plain == [d for d, _ in scored]

    def test_rrf_plain_equals_scored_stripped(self):
        from hybrid_search import rrf_rank, rrf_rank_scored
        a = ["x", "y", "z"]
        b = ["z", "x", "y"]
        for rankings in ([a], [a, b]):
            for top_k in (None, 2):
                plain = rrf_rank(rankings, top_k=top_k)
                scored = rrf_rank_scored(rankings, top_k=top_k)
                assert plain == [d for d, _ in scored]

    def test_tfidf_plain_equals_scored_stripped(self):
        from knowledge_web import _tfidf_rank, _tfidf_rank_scored
        docs = [_Doc(t) for t in self.TEXTS]
        for top_k in (None, 2):
            plain = _tfidf_rank(self.QUERY, docs, top_k=top_k)
            scored = _tfidf_rank_scored(self.QUERY, docs, top_k=top_k)
            assert plain == [d for d, _ in scored]
        # No-signal path: input order, unscored, top_k ignored.
        assert _tfidf_rank_scored("the of", docs, top_k=1) == \
            [(d, 0.0) for d in docs]

    def test_query_lessons_wrapper_equivalence(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from knowledge_web import (query_lessons, query_lessons_scored,
                                   record_tiered_lesson)
        for t in self.TEXTS:
            record_tiered_lesson(t, task_type="agenda", outcome="done",
                                 source_goal="g1")
        plain = query_lessons(self.QUERY, n=3, task_type="agenda")
        scored = query_lessons_scored(self.QUERY, n=3, task_type="agenda")
        assert [l.lesson_id for l in plain] == \
            [l.lesson_id for l, _ in scored]
        assert len(scored) == 3


class TestCameraLog:
    def _pin_run_dir(self, monkeypatch, tmp_path):
        import runs
        rd = tmp_path / "runs" / "r-cam"
        rd.mkdir(parents=True)
        monkeypatch.setattr(runs, "current_run_dir", lambda: rd)
        return rd

    def test_frame_written_with_honest_shares(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        rd = self._pin_run_dir(monkeypatch, tmp_path)
        from camera_log import log_fork_frame
        ok = log_fork_frame(
            "recall.lesson_selection",
            query="q" * 500,
            axes={"slice": "loop"},
            candidates={
                "agenda": [
                    {"lesson_id": "a", "text": "t", "score": 3.0},
                    {"lesson_id": "b", "text": "u", "score": 1.0},
                ],
                "flat": [{"lesson_id": "", "text": "f", "score": None}],
            },
            chosen={"lesson_ids": ["a"]},
            extra={"ranker": "hybrid"},
        )
        assert ok is True
        lines = (rd / "source" / "camera_frames.jsonl").read_text() \
            .strip().splitlines()
        assert len(lines) == 1
        frame = json.loads(lines[0])
        assert frame["fork"] == "recall.lesson_selection"
        assert len(frame["query_preview"]) == 200  # preview cap
        agenda = frame["candidates"]["agenda"]
        assert agenda[0]["score_share"] == 0.75
        assert agenda[1]["score_share"] == 0.25
        # Unscored sources never get fake mass.
        assert frame["candidates"]["flat"][0]["score_share"] is None
        assert frame["chosen"]["lesson_ids"] == ["a"]

    def test_killswitch_off_writes_nothing(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        rd = self._pin_run_dir(monkeypatch, tmp_path)
        import config
        monkeypatch.setattr(
            config, "get",
            lambda k, d=None: False if k == "camera.frame_log_enabled" else d)
        from camera_log import log_fork_frame
        assert log_fork_frame("recall.lesson_selection") is False
        assert not (rd / "source" / "camera_frames.jsonl").exists()

    def test_stringly_false_killswitch_respected(self, monkeypatch, tmp_path):
        # Chunk-5a lesson: YAML "false" is a truthy Python string.
        _setup(monkeypatch, tmp_path)
        rd = self._pin_run_dir(monkeypatch, tmp_path)
        import config
        monkeypatch.setattr(
            config, "get",
            lambda k, d=None: "false" if k == "camera.frame_log_enabled" else d)
        from camera_log import log_fork_frame
        assert log_fork_frame("recall.lesson_selection") is False
        assert not (rd / "source" / "camera_frames.jsonl").exists()

    def test_no_run_dir_drops_frame(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        monkeypatch.setattr(runs, "current_run_dir", lambda: None)
        from camera_log import log_fork_frame
        assert log_fork_frame("recall.lesson_selection",
                              chosen={"lesson_ids": ["a"]}) is False
        assert not list(tmp_path.rglob("camera_frames.jsonl"))

    def test_stale_run_dir_drops_frame_no_orphan(self, monkeypatch, tmp_path):
        # Review F7: a ContextVar pointing at a deleted/never-created run
        # dir must NOT be resurrected as an orphan by the frame write.
        _setup(monkeypatch, tmp_path)
        import runs
        stale = tmp_path / "runs" / "r-deleted"  # never created
        monkeypatch.setattr(runs, "current_run_dir", lambda: stale)
        from camera_log import log_fork_frame
        assert log_fork_frame("recall.lesson_selection",
                              chosen={"lesson_ids": ["a"]}) is False
        assert not stale.exists()

    def test_camera_uses_short_lock_timeout(self, monkeypatch, tmp_path):
        # Review F1: the camera must never wait the global 30s file-lock
        # deadline — a stuck lock costs the frame, not the recall.
        _setup(monkeypatch, tmp_path)
        self._pin_run_dir(monkeypatch, tmp_path)
        import file_lock
        import camera_log
        captured = {}

        def fake_append(path, line, *, timeout_s=None):
            captured["timeout_s"] = timeout_s

        monkeypatch.setattr(file_lock, "locked_append", fake_append)
        assert camera_log.log_fork_frame("recall.lesson_selection") is True
        assert captured["timeout_s"] == camera_log._LOCK_TIMEOUT_S
        assert captured["timeout_s"] <= 5.0

    def test_locked_append_timeout_override_fails_fast(self, monkeypatch,
                                                       tmp_path):
        # The seam behind F1: a per-call deadline override beats the
        # configured 30s default when the lock is genuinely held.
        _setup(monkeypatch, tmp_path)
        import threading
        import time
        from file_lock import locked_write, locked_append, FileLockTimeout
        target = tmp_path / "contended.jsonl"
        entered = threading.Event()
        release = threading.Event()

        def holder():
            with locked_write(target):
                entered.set()
                release.wait(timeout=10)

        t = threading.Thread(target=holder, daemon=True)
        t.start()
        assert entered.wait(timeout=5)
        start = time.monotonic()
        with pytest.raises(FileLockTimeout):
            locked_append(target, "x", timeout_s=0.2)
        assert time.monotonic() - start < 5.0  # not the 30s default
        release.set()
        t.join(timeout=5)

    def test_garbage_never_raises(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        self._pin_run_dir(monkeypatch, tmp_path)
        from camera_log import log_fork_frame
        # Non-serializable score object → json.dumps raises → caught.
        assert log_fork_frame(
            "recall.lesson_selection",
            candidates={"agenda": [
                {"lesson_id": "a", "text": "t", "score": object()}]},
        ) is False


class TestRecallCameraLiveness:
    def test_loop_recall_drops_one_joinable_frame(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import memory
        import runs as runs_module
        from knowledge_web import record_tiered_lesson
        from recall import recall

        tl = record_tiered_lesson(
            "camera liveness lesson about rsync trees",
            task_type="agenda", outcome="done", source_goal="g1")
        monkeypatch.setattr(memory, "load_lessons", lambda **kw: [])
        rd = tmp_path / "runs" / "r-live"
        rd.mkdir(parents=True)
        monkeypatch.setattr(runs_module, "current_run_dir", lambda: rd)

        events = []
        with patch("captains_log.log_event",
                   side_effect=lambda et, **kw: events.append((et, kw))):
            r = recall("rsync trees", slice="loop")

        assert "camera liveness lesson" in r.lessons
        frames = (rd / "source" / "camera_frames.jsonl").read_text() \
            .strip().splitlines()
        assert len(frames) == 1
        frame = json.loads(frames[0])
        assert frame["fork"] == "recall.lesson_selection"
        # Chosen == rendered (same law as citations), joinable by id.
        assert frame["chosen"]["lesson_ids"] == [tl.lesson_id]
        agenda = frame["candidates"]["agenda"]
        assert any(c["lesson_id"] == tl.lesson_id for c in agenda)
        assert all(isinstance(c["score"], (int, float)) for c in agenda)
        # Review F4: logged scores are the ranker's RAW output, unrounded.
        from knowledge_web import query_lessons_scored
        raw = {l.lesson_id: s for l, s in query_lessons_scored(
            "rsync trees", n=10, task_type="agenda")}
        for c in agenda:
            assert c["score"] == raw[c["lesson_id"]]
        assert frame["extra"]["ranker"] in ("hybrid", "tfidf")
        assert frame["axes"]["substrate_chars"]["lessons"] > 0
        # The RECALL_PERFORMED stamp records that a frame was dropped.
        ctx = [kw for et, kw in events
               if et == "RECALL_PERFORMED"][0]["context"]
        assert ctx.get("camera_frame") is True

    def test_selection_matches_query_lessons_top3(self, monkeypatch, tmp_path):
        """The n=10 over-fetch is instrumentation only: what the loop slice
        renders must equal the pre-chunk-A selection (query_lessons top-3)."""
        _setup(monkeypatch, tmp_path)
        import memory
        from knowledge_web import record_tiered_lesson, query_lessons
        from recall import recall

        for t in TestScoredRankerEquivalence.TEXTS:
            record_tiered_lesson(t, task_type="agenda", outcome="done",
                                 source_goal="g1")
        monkeypatch.setattr(memory, "load_lessons", lambda **kw: [])

        events = []
        with patch("captains_log.log_event",
                   side_effect=lambda et, **kw: events.append((et, kw))):
            recall("socket retry backoff strategy", slice="loop")

        ctx = [kw for et, kw in events
               if et == "RECALL_PERFORMED"][0]["context"]
        expected = [l.lesson_id for l in query_lessons(
            "socket retry backoff strategy", n=3, task_type="agenda")]
        assert ctx["lesson_ids_cited"] == expected

    def test_fallback_frame_marked_degraded(self, monkeypatch, tmp_path):
        """Review F5: when ranked selection dies and the legacy injector
        renders lessons, the frame must say so — not record a false
        'nothing chosen' against a run that DID render lessons."""
        _setup(monkeypatch, tmp_path)
        import memory
        import knowledge_web
        import runs as runs_module
        from recall import recall

        def _boom(*a, **kw):
            raise RuntimeError("ranked retrieval down")

        monkeypatch.setattr(knowledge_web, "query_lessons_scored", _boom)
        monkeypatch.setattr(
            memory, "inject_lessons_for_task",
            lambda *a, **kw: "## Lessons from Prior Runs\n- legacy line")
        rd = tmp_path / "runs" / "r-degraded"
        rd.mkdir(parents=True)
        monkeypatch.setattr(runs_module, "current_run_dir", lambda: rd)

        with patch("captains_log.log_event"):
            r = recall("anything at all", slice="loop")

        assert "legacy line" in r.lessons
        frames = (rd / "source" / "camera_frames.jsonl").read_text() \
            .strip().splitlines()
        assert len(frames) == 1
        frame = json.loads(frames[0])
        assert frame["extra"]["degraded"] == "legacy_fallback"
        assert frame["chosen"]["lesson_ids"] == []
        # The rendered-but-uncited lessons still show in the axes.
        assert frame["axes"]["substrate_chars"]["lessons"] > 0


class TestCameraReadout:
    """Review F2/F3/F6 pins on the consumer: untyped selections join the
    verdict section, torn lines are counted not hidden, and score means
    never mix ranker families."""

    def _mk_run(self, root, name, frames, card=None, torn=False):
        rd = root / name
        (rd / "source").mkdir(parents=True)
        lines = [json.dumps(f) for f in frames]
        if torn:
            lines.append("{this line is not json")
        (rd / "source" / "camera_frames.jsonl").write_text(
            "\n".join(lines) + "\n")
        if card is not None:
            (rd / "run_card.json").write_text(json.dumps(card))
        return rd

    @staticmethod
    def _frame(ranker, source, lesson_id, score, chosen):
        return {
            "ts": "2026-07-31T00:00:00+00:00",
            "fork": "recall.lesson_selection",
            "query_preview": "q",
            "axes": {"substrate_chars": {"lessons": 10}},
            "candidates": {source: [{
                "lesson_id": lesson_id, "text": "text of the lesson",
                "score": score, "score_share": 1.0}]},
            "chosen": {"lesson_ids": chosen, "previews": []},
            "extra": {"ranker": ranker},
        }

    def test_untyped_join_torn_count_family_split(self, tmp_path, capsys):
        root = tmp_path / "runs"
        root.mkdir()
        # Run 1: agenda empty, chosen came from scored UNTYPED (F2) —
        # plus one torn line (F3). Run 2: different ranker family (F6).
        self._mk_run(root, "r1",
                     [self._frame("hybrid", "untyped", "u1", 2.0, ["u1"])],
                     card={"goal_achieved": True}, torn=True)
        self._mk_run(root, "r2",
                     [self._frame("tfidf", "agenda", "a1", 0.5, ["a1"])],
                     card={"goal_achieved": True})
        import camera_readout
        assert camera_readout.main(["--runs-root", str(root)]) == 0
        out = capsys.readouterr().out
        # F3: torn line surfaced, not silently dropped.
        assert "1 unparsable frame line(s) skipped" in out
        # F6: one verdict row per ranker family, never a blended mean.
        rows = [l for l in out.splitlines() if l.strip().startswith("achieved")]
        assert len(rows) == 2
        hybrid_row = next(l for l in rows if "[hybrid" in l)
        tfidf_row = next(l for l in rows if "[tfidf" in l)
        # F2: the untyped selection carries its score into the join.
        assert "chosen 2.0000" in hybrid_row
        assert "chosen 0.5000" in tfidf_row

    def test_unreadable_card_counted_not_uncurated(self, tmp_path, capsys):
        # Probed 2026-08-18 (loop_report r2 MEDIUM): a torn run_card.json
        # silently rendered the run as uncurated — same defect family as
        # torn frame lines, which the readout already announces. The card
        # stays None (verdict sections skip it) but the loss is printed.
        root = tmp_path / "runs"
        root.mkdir()
        rd = self._mk_run(root, "r1",
                          [self._frame("hybrid", "agenda", "a1", 1.0, ["a1"])])
        (rd / "run_card.json").write_bytes(b'{"goal_achieved": true \xff torn')
        # Tainted-but-VALID twin: parses under plain json.loads, so only
        # loads_clean refusal makes this row count as unreadable.
        rd2 = self._mk_run(root, "r2",
                           [self._frame("hybrid", "agenda", "a2", 1.0, ["a2"])])
        (rd2 / "run_card.json").write_bytes(
            b'{"goal_achieved": true, "note": "fine \xff\x80"}')
        import camera_readout
        assert camera_readout.main(["--runs-root", str(root)]) == 0
        out = capsys.readouterr().out
        assert "2 run_card.json file(s) unreadable" in out
        assert "WERE curated" in out
