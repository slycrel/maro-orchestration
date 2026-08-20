"""Tests for maro-gc memory garbage collection (Phase 25)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import gc_memory
from gc_memory import (
    GCReport,
    DEFAULT_OUTCOMES_RETAIN_DAYS,
    _gc_narrative_logs,
    _gc_outcomes,
    _gc_tiered_lessons,
    run_gc,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mem_dir(tmp_path) -> Path:
    """Return the memory dir that _memory_dir() will resolve to."""
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    return mem


def _write_outcome(mem: Path, days_ago: int = 0, status: str = "done") -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    line = json.dumps({"goal": "test", "status": status, "recorded_at": ts})
    with open(mem / "outcomes.jsonl", "a") as f:
        f.write(line + "\n")


def _write_narrative(mem: Path, days_ago: int) -> Path:
    date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    f = mem / f"{date}.md"
    f.write_text(f"# Log {date}\n\nContent here.\n")
    return f


def _write_tiered_lesson(mem: Path, tier: str, score: float) -> None:
    import uuid
    from datetime import date
    tier_dir = mem / tier
    tier_dir.mkdir(exist_ok=True)
    line = json.dumps({
        "lesson_id": str(uuid.uuid4())[:8],
        "task_type": "test",
        "outcome": "success",
        "lesson": "test lesson",
        "source_goal": "test",
        "confidence": 0.7,
        "tier": tier,
        "score": score,
        "last_reinforced": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "sessions_validated": 0,
        "times_applied": 0,
        "times_reinforced": 0,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "acquired_for": None,
    })
    with open(tier_dir / "lessons.jsonl", "a") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# _gc_outcomes
# ---------------------------------------------------------------------------

def test_gc_outcomes_empty_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    total, removed, freed = _gc_outcomes(dry_run=True)
    assert total == 0
    assert removed == 0


def test_gc_outcomes_removes_old_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_outcome(mem, days_ago=100)  # old
    _write_outcome(mem, days_ago=100)  # old
    _write_outcome(mem, days_ago=1)    # recent
    total, removed, freed = _gc_outcomes(retain_days=90, dry_run=True)
    assert total == 3
    assert removed == 2


def test_gc_outcomes_dry_run_does_not_modify(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_outcome(mem, days_ago=100)
    _write_outcome(mem, days_ago=1)
    path = mem / "outcomes.jsonl"
    original_size = path.stat().st_size
    _gc_outcomes(retain_days=90, dry_run=True)
    assert path.stat().st_size == original_size


def test_gc_outcomes_live_run_removes_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_outcome(mem, days_ago=100)
    _write_outcome(mem, days_ago=1)
    total, removed, freed = _gc_outcomes(retain_days=90, dry_run=False)
    assert removed == 1
    # Verify file only has recent entry
    lines = (mem / "outcomes.jsonl").read_text().splitlines()
    assert len(lines) == 1
    remaining = json.loads(lines[0])
    assert remaining["status"] == "done"


def test_gc_outcomes_keeps_all_recent(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    for i in range(5):
        _write_outcome(mem, days_ago=i)
    total, removed, freed = _gc_outcomes(retain_days=90, dry_run=True)
    assert removed == 0


# ---------------------------------------------------------------------------
# _gc_narrative_logs
# ---------------------------------------------------------------------------

def test_gc_narrative_logs_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    found, removed = _gc_narrative_logs(retain_days=180, dry_run=True)
    assert found == 0


def test_gc_narrative_logs_removes_old(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_narrative(mem, days_ago=200)  # old
    _write_narrative(mem, days_ago=5)    # recent
    found, removed = _gc_narrative_logs(retain_days=180, dry_run=True)
    assert found == 2
    assert removed == 1


def test_gc_narrative_logs_dry_run_keeps_files(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    old = _write_narrative(mem, days_ago=200)
    _gc_narrative_logs(retain_days=180, dry_run=True)
    assert old.exists()


def test_gc_narrative_logs_live_deletes(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    old = _write_narrative(mem, days_ago=200)
    recent = _write_narrative(mem, days_ago=5)
    _gc_narrative_logs(retain_days=180, dry_run=False)
    assert not old.exists()
    assert recent.exists()


# ---------------------------------------------------------------------------
# _gc_tiered_lessons
# ---------------------------------------------------------------------------

def test_gc_tiered_lessons_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    removed = _gc_tiered_lessons(dry_run=True)
    assert removed == 0


def test_gc_tiered_lessons_removes_below_threshold(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_tiered_lesson(mem, "medium", score=0.1)  # below GC_THRESHOLD (0.2)
    _write_tiered_lesson(mem, "medium", score=0.5)  # above threshold
    removed = _gc_tiered_lessons(dry_run=True)
    assert removed == 1


def test_gc_tiered_lessons_live_removes(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_tiered_lesson(mem, "medium", score=0.1)
    _write_tiered_lesson(mem, "medium", score=0.8)
    _gc_tiered_lessons(dry_run=False)
    lines = (mem / "medium" / "lessons.jsonl").read_text().splitlines()
    assert len(lines) == 1
    kept = json.loads(lines[0])
    assert kept["score"] == 0.8


# ---------------------------------------------------------------------------
# run_gc (top-level)
# ---------------------------------------------------------------------------

def test_run_gc_returns_report(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    report = run_gc(dry_run=True)
    assert isinstance(report, GCReport)
    assert report.dry_run is True


def test_run_gc_summary_contains_sections(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    report = run_gc(dry_run=True)
    summary = report.summary()
    assert "outcomes" in summary
    assert "lessons" in summary
    assert "narratives" in summary
    assert "DRY RUN" in summary


def test_run_gc_live_cleans_old_data(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_outcome(mem, days_ago=100)
    _write_outcome(mem, days_ago=1)
    report = run_gc(outcomes_retain_days=90, dry_run=False)
    assert report.outcomes_removed == 1
    assert report.outcomes_retained == 1
    assert report.dry_run is False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_main_status_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    gc_memory.main(["status"])
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "outcomes" in out


def test_main_no_args_shows_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    gc_memory.main([])
    out = capsys.readouterr().out
    assert "DRY RUN" in out


def test_main_run_dry_run_flag(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    gc_memory.main(["run", "--dry-run"])
    out = capsys.readouterr().out
    assert "DRY RUN" in out


def test_main_run_yes_executes(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_outcome(mem, days_ago=100)
    gc_memory.main(["run", "--yes", "--outcomes-retain-days", "90"])
    out = capsys.readouterr().out
    assert "GC Summary" in out
    assert "DRY RUN" not in out


# ---------------------------------------------------------------------------
# _gc_outcomes byte-safety (2026-08-20 destructive-rewrite sweep triage)
# ---------------------------------------------------------------------------

class TestOutcomesGcSurvivesATornByte:
    """One torn byte used to DISABLE outcome retention forever, silently.

    Probed live 2026-08-20: a healthy store reported (2, 1, 0); after one
    crash-torn append the same call reported (0, 0, 0) — "nothing to
    collect" — with no log line at all. The store then grew without bound
    while GC reported success on every run.
    """

    def _torn(self, mem: Path) -> None:
        with open(mem / "outcomes.jsonl", "ab") as f:
            f.write(b'{"goal": "torn\xff", "status": "done"}\n')

    def test_a_torn_byte_does_not_disable_collection(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        _write_outcome(mem, days_ago=200)
        _write_outcome(mem, days_ago=1)
        self._torn(mem)

        total, removed, _ = _gc_outcomes(retain_days=90, dry_run=True)

        assert total == 3, "the torn row is counted, not swallowed"
        assert removed == 1, "the old row is still collectable past a torn byte"

    def test_the_torn_row_is_never_collected(self, monkeypatch, tmp_path):
        """A row whose bytes we cannot trust cannot authorize its own delete."""
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        _write_outcome(mem, days_ago=200)
        self._torn(mem)

        _gc_outcomes(retain_days=90, dry_run=False)

        after = (mem / "outcomes.jsonl").read_bytes()
        assert b"\xff" in after, "the torn row survived the trim"
        assert b'"days_ago"' not in after

    def test_the_loss_is_announced(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        _write_outcome(mem, days_ago=1)
        self._torn(mem)

        with caplog.at_level("WARNING"):
            _gc_outcomes(retain_days=90, dry_run=True)

        assert any("unparseable" in r.message or "byte-tainted" in r.message
                   for r in caplog.records), caplog.text

    def test_a_tainted_but_valid_row_is_kept_conservatively(self, monkeypatch, tmp_path):
        """It parses as JSON, but its timestamp is not trustworthy — keep it."""
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        tainted = json.dumps(
            {"goal": "tainted", "status": "done", "recorded_at": old}
        ).encode().replace(b"tainted", b"tain\xffed")
        with open(mem / "outcomes.jsonl", "ab") as f:
            f.write(tainted + b"\n")

        total, removed, _ = _gc_outcomes(retain_days=90, dry_run=False)

        assert total == 1 and removed == 0
        after = (mem / "outcomes.jsonl").read_bytes()
        assert tainted in after, "kept verbatim, not re-dumped as clean escapes"
        assert b"udcff" not in after.lower()


class TestOutcomesGcReportsWhatItActuallyDid:
    """Adversarial round 2026-08-20 (Skeptic + Experimentalist).

    The finding was half right, and the half that held is the one that
    mattered. The claim "a post-scan append is deleted with the old row it
    equals" cannot cost data — an outcomes line identical to an old one
    carries that same old timestamp, so collecting it is correct. But the
    RETURNED COUNTS were computed from the out-of-lock scan, so GC could
    delete two rows and report one. Reclassifying inside the lock removes
    the value-identity dependence and makes the counts describe the
    mutation that actually happened.
    """

    def test_the_counts_describe_the_rewrite_not_the_scan(self, monkeypatch, tmp_path):
        import gc_memory as _gc
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        old_line = json.dumps({
            "goal": "dup", "status": "done",
            "recorded_at": (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()})
        (mem / "outcomes.jsonl").write_text(old_line + "\n")

        real = _gc._store_text
        landed = {}

        def racing(path):
            text = real(path)
            from file_lock import locked_append
            locked_append(path, old_line)   # lands after the scan
            landed["yes"] = True
            return text

        monkeypatch.setattr(_gc, "_store_text", racing)
        total, removed, _ = _gc._gc_outcomes(retain_days=90, dry_run=False)

        assert landed, "the racing hook never ran — test is vacuous"
        assert (total, removed) == (2, 2), (
            f"reported ({total}, {removed}) for a rewrite that removed 2 rows")

    def test_uncollectable_rows_reach_the_operator(self, monkeypatch, tmp_path):
        """They can never age out, so a store accumulating them grows without
        bound. The retention decree forbids deleting them — so they must at
        least be visible, not just logged."""
        from gc_memory import run_gc
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        _write_outcome(mem, days_ago=1)
        with open(mem / "outcomes.jsonl", "ab") as f:
            f.write(b'{"goal": "torn\xff", "status": "done"}\n')

        report = run_gc(dry_run=True)

        assert report.outcomes_uncollectable == 1
        assert "UNREADABLE" in report.summary()

    def test_a_json_value_that_is_not_an_object_is_kept_not_collected(self, monkeypatch, tmp_path):
        from gc_memory import _gc_outcomes as gc
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        (mem / "outcomes.jsonl").write_text("[]\nnull\n")

        total, removed, _ = gc(retain_days=90, dry_run=False)

        assert (total, removed) == (2, 0)
        assert "[]" in (mem / "outcomes.jsonl").read_text()


class TestFreedBytesDescribeTheRewrite:
    """Adversarial r2 (2026-08-20, 3/3): `original_size` was sampled before
    the lock, so a concurrent RETAINED append was charged against GC's freed
    count — a successful collection reported to the operator as having GROWN
    the store. Probed: (2, 1, -4097)."""

    def test_a_concurrent_retained_append_does_not_make_freed_negative(
            self, monkeypatch, tmp_path):
        """The append must land AFTER the pre-lock size sample, or this test
        cannot fail.

        Adversarial r3 (2026-08-20, Minimalist, probed): the first version of
        this test appended inside `_store_text`, which in the pre-fix code
        ran BEFORE `original_size = path.stat().st_size` — so the old
        implementation's own snapshot already included the big row and
        reported a positive `freed`. The test passed on the exact defect it
        names. Hooking `locked_rmw` puts the append in the real window:
        after the size sample, before the rewrite.
        """
        import gc_memory as _gc
        import file_lock as _fl
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        _write_outcome(mem, days_ago=200)
        big = json.dumps({"goal": "x" * 4000, "status": "done",
                          "recorded_at": datetime.now(timezone.utc).isoformat()})
        real_rmw = _fl.locked_rmw
        landed = {}

        def racing_rmw(path, fn, *a, **kw):
            _fl.locked_append(path, big)
            landed["yes"] = True
            return real_rmw(path, fn, *a, **kw)

        monkeypatch.setattr(_fl, "locked_rmw", racing_rmw)
        total, removed, freed = _gc._gc_outcomes(retain_days=90, dry_run=False)

        assert landed, "the racing hook never ran — test is vacuous"
        assert removed == 1
        assert freed > 0, f"a collection that removed a row reported freed={freed}"
        assert (mem / "outcomes.jsonl").read_text().count("xxxx") > 0, (
            "the concurrent retained row was destroyed by the rewrite")

    def test_freed_matches_the_bytes_actually_removed(self, monkeypatch, tmp_path):
        from gc_memory import _gc_outcomes as gc
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        _write_outcome(mem, days_ago=200)
        _write_outcome(mem, days_ago=1)
        before = (mem / "outcomes.jsonl").stat().st_size

        _, removed, freed = gc(retain_days=90, dry_run=False)

        after = (mem / "outcomes.jsonl").stat().st_size
        assert removed == 1
        assert freed == before - after


class TestTheUncollectableCountIsAbsoluteNotADelta:
    """Adversarial r3 (2026-08-20, Skeptic + Architect, probed): GC announced
    the unlocked count and then announced `locked - unlocked` as though the
    difference were itself a count. A row repaired between the two reads
    produced `gc: -1 unparseable/byte-tainted row(s) ... kept` — a negative
    row count, in the one warning whose job is to tell an operator how many
    rows are permanently stuck."""

    def test_a_row_repaired_before_the_lock_never_reports_a_negative_count(
            self, monkeypatch, tmp_path, caplog):
        import file_lock as _fl
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        out = mem / "outcomes.jsonl"
        _write_outcome(mem, days_ago=200)          # collectable
        _write_outcome(mem, days_ago=1)            # retained
        with open(out, "ab") as fh:                # two torn rows
            fh.write(b'{"goal": "torn-a\xff", "recorded_at": "x"}\n')
            fh.write(b'{"goal": "torn-b\xff", "recorded_at": "x"}\n')

        real_rmw = _fl.locked_rmw
        healed = {}

        good = json.dumps({"goal": "repaired", "status": "done",
                           "recorded_at": datetime.now(timezone.utc).isoformat()})

        def healing_rmw(path, fn, *a, **kw):
            lines = path.read_bytes().split(b"\n")
            lines = [good.encode() if b"torn-a" in l else l for l in lines]
            path.write_bytes(b"\n".join(lines))    # one torn row repaired
            healed["yes"] = True
            return real_rmw(path, fn, *a, **kw)

        monkeypatch.setattr(_fl, "locked_rmw", healing_rmw)
        caplog.clear()
        with caplog.at_level("WARNING"):
            _gc_outcomes(retain_days=90, dry_run=False)

        assert healed, "the healing hook never ran — test is vacuous"
        counts = [r.args[0] for r in caplog.records
                  if "unparseable/byte-tainted" in r.msg]
        assert counts, "the uncollectable rows were never announced"
        assert all(c >= 0 for c in counts), (
            f"GC announced a negative row count: {counts}")
        assert counts[-1] == 1, (
            f"the announced count is not the locked absolute one: {counts}")


class TestALockedPassThatRemovesNothingDoesNotRewrite:
    """Adversarial r3 (2026-08-20, Architect, probed): the unlocked pre-scan
    commits GC to `locked_rmw`. If the window closes — the collectable row is
    gone by the time the lock is held — the old shape still rejoined and
    rewrote, normalizing framing. A snapshot without a trailing newline came
    back one byte LARGER and GC reported `freed=-1` for a collection that
    collected nothing."""

    def test_the_bytes_are_returned_untouched(self, monkeypatch, tmp_path):
        import file_lock as _fl
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        out = mem / "outcomes.jsonl"
        _write_outcome(mem, days_ago=200)

        # Between the unlocked scan and the lock, the collectable row is
        # replaced by a retained one with NO trailing newline.
        recent = json.dumps({"goal": "kept", "status": "done",
                             "recorded_at": datetime.now(timezone.utc).isoformat()})
        real_rmw = _fl.locked_rmw
        raced = {}

        def racing_rmw(path, fn, *a, **kw):
            path.write_text(recent, encoding="utf-8")   # no trailing "\n"
            raced["before"] = path.read_bytes()
            return real_rmw(path, fn, *a, **kw)

        monkeypatch.setattr(_fl, "locked_rmw", racing_rmw)
        total, removed, freed = _gc_outcomes(retain_days=90, dry_run=False)

        assert raced, "the racing hook never ran — test is vacuous"
        assert removed == 0
        assert freed == 0, f"a no-op collection reported freed={freed}"
        assert out.read_bytes() == raced["before"], (
            "a locked pass that removed nothing still rewrote the store")


class TestANoOpLockedPassDoesNotTouchTheFile:
    """Adversarial r4 (3 lenses, probed): r3 expressed "do not rewrite" by
    returning the text unchanged from `_trim`, and `locked_rmw` wrote it back
    anyway — same bytes, new inode, new mtime, and every watcher told the
    store changed. The r3 test compared bytes, so it passed on the defect."""

    def test_atomic_write_is_never_called(self, monkeypatch, tmp_path):
        import file_lock as _fl
        import gc_memory as _gc
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        out = mem / "outcomes.jsonl"
        _write_outcome(mem, days_ago=200)

        recent = json.dumps({"goal": "kept", "status": "done",
                             "recorded_at": datetime.now(timezone.utc).isoformat()})
        real_rmw, real_write = _fl.locked_rmw, _fl.atomic_write
        writes, raced = [], {}

        def spy_write(path, text, **kw):
            writes.append(str(path))
            return real_write(path, text, **kw)

        def racing_rmw(path, fn, **kw):
            path.write_text(recent, encoding="utf-8")   # no trailing "\n"
            raced["stat"] = path.stat()
            return real_rmw(path, fn, **kw)

        monkeypatch.setattr(_fl, "atomic_write", spy_write)
        monkeypatch.setattr(_fl, "locked_rmw", racing_rmw)
        total, removed, freed = _gc._gc_outcomes(retain_days=90, dry_run=False)

        assert raced, "the racing hook never ran — test is vacuous"
        assert (removed, freed) == (0, 0)
        assert not writes, f"a no-op collection still rewrote the store: {writes}"
        assert out.stat().st_ino == raced["stat"].st_ino, "the inode changed"


class TestAFailedRewriteStillReportsWhatIsStuck:
    """Adversarial r4 (2 lenses, probed): r3 moved the announcement and the
    `uncollectable` stat below the lock, so a failed lock/read/write returned
    (total, 0, 0) with no warning and no stat — GCReport then said zero
    unreadable rows while one sits in the store forever. The unlocked
    classification is the only one available when the locked pass never
    completed, and it is worth strictly more than silence."""

    def test_the_uncollectable_count_survives_an_oserror(
            self, monkeypatch, tmp_path, caplog):
        import file_lock as _fl
        import gc_memory as _gc
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        _write_outcome(mem, days_ago=200)
        with open(mem / "outcomes.jsonl", "ab") as fh:
            fh.write(b'{"goal": "torn\xff", "recorded_at": "x"}\n')

        def boom(path, fn, **kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(_fl, "locked_rmw", boom)
        stats: dict = {}
        with caplog.at_level("WARNING"):
            total, removed, freed = _gc._gc_outcomes(
                retain_days=90, dry_run=False, stats=stats)

        assert (removed, freed) == (0, 0)
        assert stats.get("uncollectable") == 1, (
            "the report would have claimed zero unreadable rows")
        assert any("rewrite failed" in r.msg for r in caplog.records)
        assert any("unparseable/byte-tainted" in r.msg for r in caplog.records)


class TestATimestampReadOutOfALaunderedCopyCannotAuthorizeADelete:
    """Adversarial r9, applied to the sibling the finding did not name.
    `_classify` parsed `raw.strip()` and this verb DELETES on the timestamp
    it reads there — so a row whose bytes JSON forbids (U+2028 and friends
    are not JSON whitespace) had its `recorded_at` trusted anyway. The
    conservative branch already exists; the row belongs in it."""

    def test_a_row_wrapped_in_json_forbidden_whitespace_is_kept(
            self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        row = json.dumps({"goal": "old", "status": "done", "recorded_at": old})
        (mem / "outcomes.jsonl").write_text(" " + row + "\n",
                                            encoding="utf-8")

        with caplog.at_level("WARNING"):
            total, removed, _ = _gc_outcomes(retain_days=90, dry_run=False)

        assert removed == 0, "a row this verb could not read was collected"
        assert " " in (mem / "outcomes.jsonl").read_text(encoding="utf-8")
        assert any("kept, never collected" in r.message for r in caplog.records)

    def test_a_line_of_only_whitespace_survives_the_rewrite(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _mem_dir(tmp_path)
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        rows = [json.dumps({"goal": "a", "status": "done", "recorded_at": old}),
                " ",
                json.dumps({"goal": "b", "status": "done",
                            "recorded_at": recent})]
        (mem / "outcomes.jsonl").write_text("\n".join(rows) + "\n",
                                            encoding="utf-8")

        _gc_outcomes(retain_days=90, dry_run=False)

        after = (mem / "outcomes.jsonl").read_text(encoding="utf-8")
        assert " " in after, "a row of Unicode whitespace was deleted"
        assert '"goal": "b"' in after and '"goal": "a"' not in after
