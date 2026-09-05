"""Lineage-scoped memory (feature-lineage-memory, 2026-09-05).

A goal can follow a prior run (`--after`); lessons minted from a run are
scoped to the run's lineage root; recall walks own → parents → root →
workspace. Same feature, same fixture shape, as the Go engine's
TestLineageScopesRecall.
"""
from __future__ import annotations

import json

import pytest


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)


def _make_run(goal, *, origin=None, handle_id=None):
    import runs
    import uuid
    handle_id = handle_id or uuid.uuid4().hex[:8]
    runs.create_run_dir(handle_id, prompt=goal,
                        extra_metadata={"origin": origin} if origin else None)
    return handle_id


class TestLineageRoot:
    def test_a_root_run_is_its_own_root(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from recall import lineage_root
        a = _make_run("first question")
        assert lineage_root(a) == a
        assert lineage_root("00000000") == ""   # unknown → workspace, never a guess
        assert lineage_root("") == ""

    def test_the_root_is_reached_through_the_parent_chain(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from recall import lineage_root
        a = _make_run("first")
        b = _make_run("second", origin={"parent_handle_id": a, "parent_goal": "first"})
        c = _make_run("third", origin={"parent_handle_id": b, "parent_goal": "second"})
        assert lineage_root(b) == a
        assert lineage_root(c) == a

    def test_a_cycle_and_a_ghost_parent_stop_the_walk(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from recall import lineage_root
        from recall import UNRESOLVED_LINEAGE
        x = _make_run("x", handle_id="aaaa0001", origin={"parent_handle_id": "aaaa0002"})
        y = _make_run("y", handle_id="aaaa0002", origin={"parent_handle_id": "aaaa0001"})
        # a cycle is not a lineage: every member resolves to the SAME
        # sentinel, which is no run's root (review 2026-09-05: it used to
        # be y from x and x from y — mutually invisible mints)
        assert lineage_root(x) == lineage_root(y) == UNRESOLVED_LINEAGE + "cycle:aaaa0001"
        z = _make_run("z", handle_id="aaaa0003", origin={"parent_handle_id": "aaaa0001"})
        assert lineage_root(z) == lineage_root(x)   # a chain INTO the cycle, too
        assert lineage_root(x) not in (x, y, z)
        g = _make_run("g", origin={"parent_handle_id": "deadbeef"})
        assert lineage_root(g) == g        # a parent the workspace does not hold


class TestLessonLineageFilter:
    def test_lineage_scoped_lessons_reach_their_lineage_only(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from knowledge_web import record_tiered_lesson, query_lessons_scored
        wide = record_tiered_lesson("Always cite the file and line.", "general", "done",
                                    source_goal="manual")
        scoped = record_tiered_lesson("The answer lives in the second paragraph.", "general",
                                      "done", source_goal="manual", lineage="root0001")
        assert wide.lineage == "" and scoped.lineage == "root0001"
        # the row round-trips through disk with the field
        rows = [json.loads(l) for l in (tmp_path / "memory" / "lessons_medium.jsonl").read_text().splitlines() if l.strip()] \
            if (tmp_path / "memory" / "lessons_medium.jsonl").exists() else []
        if rows:
            assert {r.get("lineage", "") for r in rows} == {"", "root0001"}
        ids = lambda scored: {l.lesson_id for l, _ in scored}
        q = "where does the answer live, cite file and line"
        assert ids(query_lessons_scored(q, n=10, lineage="root0001")) == {wide.lesson_id, scoped.lesson_id}
        assert ids(query_lessons_scored(q, n=10, lineage="other")) == {wide.lesson_id}
        assert ids(query_lessons_scored(q, n=10)) == {wide.lesson_id}          # a stranger
        assert ids(query_lessons_scored(q, n=10, lineage=None)) == {wide.lesson_id}


class TestRecallWalksTheLineage:
    """The seam: the executing run's lineage (from its own stamped origin —
    the loop calls recall() without one) selects the lesson pool."""

    def _pin(self, monkeypatch, handle_id):
        import runs
        monkeypatch.setattr(runs, "current_handle_id", lambda: handle_id)

    def test_follow_up_recalls_the_lineage_lesson_and_a_stranger_does_not(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from knowledge_web import record_tiered_lesson
        from recall import recall
        a = _make_run("summarise the go.dev version endpoint")
        record_tiered_lesson("The go.dev VERSION endpoint's second line is a timestamp.",
                             "agenda", "done", source_goal="summarise the go.dev version endpoint",
                             lineage=a)
        # a decayed (graveyard-band) lineage lesson rides the same rule
        from knowledge_web import MemoryTier, _mutate_tiered_lessons
        gy = record_tiered_lesson("Decayed lineage note: the VERSION endpoint timestamp line is UTC.",
                                  "agenda", "done", source_goal="s", lineage=a)
        def _decay(lessons):
            for l in lessons:
                if l.lesson_id == gy.lesson_id:
                    l.score = 0.35
            return lessons
        _mutate_tiered_lessons(MemoryTier.MEDIUM, _decay)
        b = _make_run("what does its second line say", origin={"parent_handle_id": a, "parent_goal": "summarise"})
        self._pin(monkeypatch, b)
        rb = recall("what does the go.dev VERSION endpoint's second line say", slice="loop")
        assert "timestamp line is UTC" in rb.graveyard
        assert rb.sources["lineage"] == a
        assert rb.thread is not None and rb.thread.parent_handle_id == a
        assert "second line is a timestamp" in rb.lessons
        c = _make_run("what does the go.dev VERSION endpoint's second line say")
        self._pin(monkeypatch, c)
        rc = recall("what does the go.dev VERSION endpoint's second line say", slice="loop")
        assert rc.sources["lineage"] == c
        assert "second line is a timestamp" not in rc.lessons
        assert "timestamp line is UTC" not in rc.graveyard
        # the record says scope excluded it — not merely that it is absent
        assert rc.sources["lineage_excluded"] == 2   # the live row and the decayed one
        assert rb.sources["lineage_excluded"] == 0

    def test_two_levels_down_still_reaches_the_root_lesson(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from knowledge_web import record_tiered_lesson
        from recall import recall
        a = _make_run("root goal")
        record_tiered_lesson("Root lineage lesson about the endpoint timestamp line.", "agenda",
                             "done", source_goal="root goal", lineage=a)
        b = _make_run("second", origin={"parent_handle_id": a})
        d = _make_run("third", origin={"parent_handle_id": b})
        self._pin(monkeypatch, d)
        r = recall("endpoint timestamp line", slice="loop")
        assert r.sources["lineage"] == a
        assert "Root lineage lesson" in r.lessons


class TestMintScopesToTheLineage:
    def test_run_level_mint_carries_the_lineage_root(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from memory import _lineage_root_for
        a = _make_run("root")
        b = _make_run("child", origin={"parent_handle_id": a})
        assert _lineage_root_for(b, "") == a
        assert _lineage_root_for("", "") == ""      # no run context: an explicit workspace mint

    def test_an_unresolvable_run_mints_closed_not_workspace_wide(self, monkeypatch, tmp_path):
        """Fail closed (review 2026-09-05): "" is the WIDEST scope, so a mint
        whose lineage cannot be resolved must not get it."""
        _setup(monkeypatch, tmp_path)
        import recall
        from knowledge_web import query_lessons_scored, record_tiered_lesson
        from memory import _lineage_root_for
        a = _make_run("root")
        assert _lineage_root_for("nope", "") == "unresolved:nope"
        assert _lineage_root_for("", "loop-9") == "unresolved:loop-9"
        monkeypatch.setattr(recall, "lineage_root", lambda ref: 1 / 0)
        assert _lineage_root_for(a, "").startswith("unresolved:error:")
        monkeypatch.undo()
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson("A lesson nobody may see until re-scoped.", "general", "done",
                                  source_goal="g", lineage=_lineage_root_for("nope", ""))
        q = "a lesson nobody may see"
        assert not [l for l, _ in query_lessons_scored(q, n=10, lineage=a) if l.lesson_id == tl.lesson_id]
        assert not [l for l, _ in query_lessons_scored(q, n=10) if l.lesson_id == tl.lesson_id]


class TestAfterFlag:
    def test_after_stamps_the_parent_on_the_new_runs_origin(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle
        import runs
        a = _make_run("the first question")
        rc = handle.main(["--dry-run", "--lane", "now", "--after", a, "a follow-up question"])
        assert rc == 0
        # the newest run dir's metadata names a as parent
        newest = max((p for p in runs.runs_root().iterdir() if p.is_dir() and not p.name.startswith(a)),
                     key=lambda p: p.stat().st_mtime)
        meta = json.loads((newest / "metadata.json").read_text(encoding="utf-8"))
        assert meta["origin"]["parent_handle_id"] == a
        assert meta["origin"]["parent_goal"] == "the first question"
        assert meta["origin"]["source"] == "cli"

    def test_after_a_run_with_unreadable_metadata_is_refused(self, monkeypatch, tmp_path, capsys):
        _setup(monkeypatch, tmp_path)
        import handle
        import runs
        a = _make_run("the first question")
        rd = runs.resolve_run_dir(a)
        (rd / "metadata.json").write_text("{not json", encoding="utf-8")
        rc = handle.main(["--dry-run", "--after", a, "x"])
        assert rc == 2
        assert "metadata.json is unreadable" in capsys.readouterr().err
        (rd / "metadata.json").write_text("[1, 2]", encoding="utf-8")   # valid JSON, not a record
        assert handle.main(["--dry-run", "--after", a, "x"]) == 2
        assert "metadata.json is unreadable" in capsys.readouterr().err

    def test_after_a_ghost_handle_is_refused_before_anything_runs(self, monkeypatch, tmp_path, capsys):
        _setup(monkeypatch, tmp_path)
        import handle
        rc = handle.main(["--dry-run", "--after", "deadbeef", "x"])
        assert rc == 2
        assert "no run with that handle_id" in capsys.readouterr().err
        assert not any(p.is_dir() for p in (tmp_path / "runs").iterdir()) if (tmp_path / "runs").exists() else True


class TestEveryInjectionSurfaceIsScoped:
    """Review 2026-09-05: ranked recall was filtered; the graveyard, the legacy
    inject API, the public query reader, and the worker-memory mirror were
    not. One rule (`_visible_in_lineage`) on every surface; a caller with no
    lineage context sees workspace rows only, never everything."""

    def _two(self, lineage="root0001"):
        from knowledge_web import record_tiered_lesson
        wide = record_tiered_lesson("Decayed wisdom about the endpoint timestamp.", "general",
                                    "done", source_goal="g")
        scoped = record_tiered_lesson("Private lineage wisdom about the endpoint timestamp.",
                                      "general", "done", source_goal="g", lineage=lineage)
        return wide, scoped

    def test_graveyard_filters_and_never_resurrects_a_foreign_row(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from knowledge_web import MemoryTier, _mutate_tiered_lessons, load_tiered_lessons, search_graveyard
        wide, scoped = self._two()
        def _decay(lessons):
            for l in lessons:
                l.score = 0.35
            return lessons
        _mutate_tiered_lessons(MemoryTier.MEDIUM, _decay)
        ids = lambda hits: {h.lesson_id for h in hits}
        assert ids(search_graveyard("endpoint timestamp wisdom")) == {wide.lesson_id}
        assert ids(search_graveyard("endpoint timestamp wisdom", lineage="other")) == {wide.lesson_id}
        assert ids(search_graveyard("endpoint timestamp wisdom", lineage="root0001")) == {wide.lesson_id, scoped.lesson_id}
        raw = lambda lid: next(l for l in load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True) if l.lesson_id == lid)
        search_graveyard("endpoint timestamp wisdom", resurrect=True, lineage="other")
        assert raw(scoped.lesson_id).score == 0.35          # a stranger's match did not reinforce it
        search_graveyard("endpoint timestamp wisdom", resurrect=True, lineage="root0001")
        assert raw(scoped.lesson_id).score > 0.35           # its own lineage may

    def test_legacy_inject_filters_and_does_not_count_a_foreign_application(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from knowledge_web import MemoryTier, inject_tiered_lessons, load_tiered_lessons
        wide, scoped = self._two()
        from knowledge_web import record_tiered_lesson
        canon = record_tiered_lesson("Long-tier private lineage wisdom about the endpoint timestamp.",
                                     "general", "done", source_goal="g", lineage="root0001",
                                     tier=MemoryTier.LONG)
        block = inject_tiered_lessons("general", "endpoint timestamp")
        assert "Decayed wisdom" in block and "Private lineage wisdom" not in block
        assert "Long-tier private" not in block
        raw = lambda lid: next(l for l in load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True) if l.lesson_id == lid)
        assert raw(scoped.lesson_id).times_applied == 0
        block = inject_tiered_lessons("general", "endpoint timestamp", lineage="root0001")
        assert "Private lineage wisdom" in block and "Long-tier private" in block
        assert raw(scoped.lesson_id).times_applied == 1

    def test_public_query_reader_can_ask_for_its_lineage(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from knowledge_web import query_lessons
        wide, scoped = self._two()
        ids = lambda ls: {l.lesson_id for l in ls}
        assert ids(query_lessons("endpoint timestamp wisdom", n=10)) == {wide.lesson_id}
        assert ids(query_lessons("endpoint timestamp wisdom", n=10, lineage="root0001")) == {wide.lesson_id, scoped.lesson_id}

    def test_worker_memory_mirror_skips_lineage_rows(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest import mock
        from memory_bridge import ingest_lessons_to_store
        from memory_sqlite import SqliteMemoryStore
        src = tmp_path / "lessons.jsonl"
        rows = [{"lesson_id": "w1", "lesson": "workspace wisdom", "task_type": "general",
                 "outcome": "done", "source_goal": "g", "confidence": 0.8, "tier": "medium",
                 "score": 1.0, "last_reinforced": "2026-01-01", "sessions_validated": 1},
                {"lesson_id": "s1", "lesson": "private lineage wisdom", "task_type": "general",
                 "outcome": "done", "source_goal": "g", "confidence": 0.8, "tier": "medium",
                 "score": 1.0, "last_reinforced": "2026-01-01", "sessions_validated": 1,
                 "lineage": "root0001"}]
        src.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        store = SqliteMemoryStore(tmp_path / "store")
        with mock.patch("memory_bridge._lessons_source_paths", return_value=[src]):
            stats = ingest_lessons_to_store(store)
        assert stats["ingested"] == 1 and stats["skipped_lineage"] == 1
        assert store.stats()["items"] == 1


class TestDedupIsLineageAware:
    def test_another_lineage_re_learning_a_lesson_mints_its_own_row(self, monkeypatch, tmp_path):
        """Review 2026-09-05: B's mint used to reinforce A's row — a lesson B
        could never recall. Same text, same task type, other lineage → new row;
        same lineage → reinforcement."""
        _setup(monkeypatch, tmp_path)
        from knowledge_web import record_tiered_lesson
        text = "Retry the fetch with exponential backoff before declaring the host down."
        a = record_tiered_lesson(text, "general", "done", source_goal="g", lineage="rootAAAA")
        b = record_tiered_lesson(text, "general", "done", source_goal="g", lineage="rootBBBB")
        w = record_tiered_lesson(text, "general", "done", source_goal="g")
        assert len({a.lesson_id, b.lesson_id, w.lesson_id}) == 3
        assert (b.lineage, w.lineage) == ("rootBBBB", "")
        again = record_tiered_lesson(text, "general", "done", source_goal="g", lineage="rootAAAA")
        assert again.lesson_id == a.lesson_id


class TestAfterDoesNotChangeNowVerdictPolicy:
    """Review 2026-09-05: `--after` stamps an origin to carry lineage; origin
    PRESENCE was the autonomous-run proxy, so an interactive follow-up
    silently gained the task-path self-verdict call."""

    def test_a_cli_origin_is_interactive(self):
        from handle import _autonomous_origin
        assert _autonomous_origin(None) is False
        assert _autonomous_origin({"source": "cli", "parent_handle_id": "aaaa0001"}) is False
        assert _autonomous_origin({"source": "telegram"}) is True
        assert _autonomous_origin({"parent_handle_id": "aaaa0001"}) is True   # a task origin: no source stamp

    def test_a_live_now_follow_up_takes_the_interactive_branch(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle
        from unittest.mock import MagicMock
        calls = []
        monkeypatch.setattr(handle, "_run_now", lambda *a, **k: {
            "status": "done", "result": "ok", "tokens_in": 1, "tokens_out": 1, "elapsed_ms": 1})
        monkeypatch.setattr(handle, "_verify_now_outcome", lambda *a, **k: calls.append("autonomous") or a[1])
        monkeypatch.setattr(handle, "_interactive_now_verdict_enabled", lambda: False)
        a = _make_run("the first question")
        handle.handle("a follow-up", force_lane="now", dry_run=False, adapter=MagicMock(),
                      origin={"source": "cli", "parent_handle_id": a, "parent_goal": "q"})
        assert calls == []
        handle.handle("a task", force_lane="now", dry_run=False, adapter=MagicMock(),
                      origin={"source": "telegram", "parent_handle_id": a})
        assert calls == ["autonomous"]


class TestPackImportNeverWidensALineageRow:
    def test_imported_lineage_is_namespaced_foreign(self, monkeypatch, tmp_path):
        """Review 2026-09-05: import rebuilt the row without lineage → "" →
        workspace-WIDE. A foreign root names no local run; it is kept,
        namespaced, and invisible until an explicit local re-scope."""
        from test_pack import _export_and_seal, _make_workspace, _write_jsonl
        from knowledge_web import MemoryTier, load_tiered_lessons, query_lessons_scored
        from pack import _foreign_lineage, import_pack
        src_ws = _make_workspace(tmp_path / "src")
        _write_jsonl(src_ws / "memory" / "long" / "lessons.jsonl",
                     [{"lesson_id": "l1", "lesson": "private lineage wisdom about batches", "task_type": "ops",
                       "outcome": "success", "source_goal": "g1", "confidence": 0.9,
                       "tier": "long", "score": 1.0, "last_reinforced": "2020-01-01",
                       "sessions_validated": 5, "lineage": "root0001"}])
        pack_path = _export_and_seal(src_ws, tmp_path)
        dst = _make_workspace(tmp_path / "dst")
        monkeypatch.setenv("MARO_MEMORY_DIR", str(dst / "memory"))
        result = import_pack(pack_path, label="l", target=dst)
        assert result["lessons_imported"][0]["outcome"] == "imported_medium"
        rows = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
        assert [r.lineage for r in rows] == ["foreign:root0001"]
        assert not query_lessons_scored("private lineage wisdom batches", n=10, lineage="root0001")
        assert not query_lessons_scored("private lineage wisdom batches", n=10)
        assert _foreign_lineage("") == "" and _foreign_lineage("foreign:x") == "foreign:x"
