"""Tests for doctor.py — environment health check."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from doctor import (
    run_doctor, _check, cleanup_workspace_skills, _skill_hash_is_stale,
    _scan_config_paths,
)
from skill_types import Skill, compute_skill_hash, skill_to_dict


class _DoctorResponse:
    content = "ok"


class _DoctorAdapter:
    def complete(self, *args, **kwargs):
        return _DoctorResponse()


@pytest.fixture(autouse=True)
def _stub_llm_health(monkeypatch):
    """Doctor output tests exercise doctor, not installed CLI authentication."""
    import bughunter
    import llm

    monkeypatch.setattr(
        llm,
        "detect_backends",
        lambda: [("subprocess", True, "test stub")],
    )
    monkeypatch.setattr(llm, "build_adapter", lambda *args, **kwargs: _DoctorAdapter())
    monkeypatch.setattr(
        bughunter,
        "run_bughunter",
        lambda: bughunter.BughunterReport(files_scanned=0),
    )


# ---------------------------------------------------------------------------
# _check helper
# ---------------------------------------------------------------------------

class TestCheck:
    def test_ok_result(self, capsys):
        result = _check("my check", True, "all good")
        captured = capsys.readouterr()
        assert result["ok"] is True
        assert result["label"] == "my check"
        assert "✓" in captured.out

    def test_fail_result(self, capsys):
        result = _check("my check", False, "broken")
        captured = capsys.readouterr()
        assert result["ok"] is False
        assert "✗" in captured.out

    def test_detail_included(self, capsys):
        _check("x", True, "some detail")
        captured = capsys.readouterr()
        assert "some detail" in captured.out

    def test_no_detail(self, capsys):
        _check("x", True)
        captured = capsys.readouterr()
        assert "x" in captured.out


# ---------------------------------------------------------------------------
# run_doctor — integration (mock heavy dependencies)
# ---------------------------------------------------------------------------

class TestRunDoctor:
    """Test that run_doctor runs without error and returns a bool."""

    def test_baseline_report_contains_required_surfaces(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        result = run_doctor()
        output = capsys.readouterr().out

        assert isinstance(result, bool)
        # Collect-then-assert rather than a loop: run_doctor() is expensive
        # enough not to repeat per surface, but a refactor that drops three
        # surfaces should name all three, not just the alphabetically-first.
        lowered = output.lower()
        missing = [s for s in (
            "Tool registry",
            "skills",
            "bughunter",
            "checks passed",
            "Escalation file surface",
            "Escalation push lane",
            "Config paths on this box",
            "Stale machine state",
            "Memory index sync",
        ) if s.lower() not in lowered]
        assert not missing, f"doctor report lost surfaces: {missing}"
        assert str(tmp_path / "output" / "escalations.jsonl") in output

    def test_stale_machine_state_detected_but_not_failing(self, capsys, monkeypatch, tmp_path):
        # A restored workspace's traveled state must be surfaced, but never
        # as a hard FAIL — a live running box legitimately has these too.
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = tmp_path / "memory"
        mem.mkdir(parents=True)
        (mem / "jobs.json").write_text("[]")
        (mem / "heartbeat-state.json").write_text("{}")
        (tmp_path / "telegram_offset.txt").write_text("123")
        (mem / "foo.lock").write_text("")
        run_doctor()
        captured = capsys.readouterr()
        assert "✓ Stale machine state" in captured.out
        assert "jobs.json" in captured.out
        assert "heartbeat-state.json" in captured.out
        assert "telegram_offset.txt" in captured.out
        assert "foo.lock" in captured.out

# ---------------------------------------------------------------------------
# _scan_config_paths — burned-in absolute paths from another machine
# ---------------------------------------------------------------------------

class TestScanConfigPaths:
    def test_existing_path_not_flagged(self, tmp_path):
        cfg = {"some": {"dir": str(tmp_path)}}
        assert _scan_config_paths(cfg) == []

    def test_missing_absolute_path_flagged(self):
        cfg = {"notify": {"binary": "/definitely/not/a/real/path/xyz123"}}
        missing = _scan_config_paths(cfg)
        assert len(missing) == 1
        assert "notify.binary=/definitely/not/a/real/path/xyz123" in missing[0]

    def test_missing_home_relative_path_flagged(self):
        cfg = {"scratch": "~/definitely-not-a-real-dir-xyz123"}
        missing = _scan_config_paths(cfg)
        assert len(missing) == 1
        assert missing[0].startswith("scratch=")

    def test_command_with_args_not_flagged_even_if_binary_missing(self):
        # Path-shaped heuristic deliberately skips anything with whitespace —
        # a shell command's argv, not a bare path.
        cfg = {"notify": {"command": "/definitely/not/real/bin --flag value"}}
        assert _scan_config_paths(cfg) == []

    def test_non_path_strings_not_flagged(self):
        cfg = {"model": {"default_tier": "cheap"}, "yolo": False, "n": 3}
        assert _scan_config_paths(cfg) == []

    def test_nested_dict_dotted_key(self, tmp_path):
        cfg = {"a": {"b": {"c": "/definitely/not/a/real/path/xyz123"}}}
        missing = _scan_config_paths(cfg)
        assert missing[0].startswith("a.b.c=")


# ---------------------------------------------------------------------------
# cleanup_workspace_skills — stale hash detection and dedup
# ---------------------------------------------------------------------------

def _make_skill(skill_id: str, name: str, correct_hash: bool = True) -> dict:
    """Build a minimal valid skill dict, optionally with a wrong stored hash."""
    skill = Skill(
        id=skill_id,
        name=name,
        description="test description",
        trigger_patterns=[],
        steps_template=["step 1"],
        source_loop_ids=[],
        created_at="2026-01-01T00:00:00Z",
        use_count=0,
        success_rate=1.0,
        content_hash="",
        tier="provisional",
        utility_score=1.0,
        failure_notes=[],
        consecutive_failures=0,
        consecutive_successes=0,
        circuit_state="closed",
        optimization_objective="",
        island="",
        variant_of=None,
        variant_wins=0,
        variant_losses=0,
    )
    d = skill_to_dict(skill)
    d["content_hash"] = compute_skill_hash(skill) if correct_hash else "aaaaaaaaaaaaaaaa"
    return d


class TestSkillHashStale:
    def test_correct_hash_not_stale(self):
        d = _make_skill("sk001", "real skill", correct_hash=True)
        assert not _skill_hash_is_stale(d)

    def test_wrong_hash_is_stale(self):
        d = _make_skill("sk002", "test fixture", correct_hash=False)
        assert _skill_hash_is_stale(d)

    def test_no_hash_not_stale(self):
        d = _make_skill("sk003", "no hash skill", correct_hash=True)
        d["content_hash"] = ""
        assert not _skill_hash_is_stale(d)


class TestCleanupWorkspaceSkills:
    def _write_skills(self, path: Path, skills: list[dict]) -> None:
        path.write_text("\n".join(json.dumps(s) for s in skills) + "\n", encoding="utf-8")

    def test_stale_hash_skills_are_removed(self, tmp_path, capsys):
        skills_file = tmp_path / "skills.jsonl"
        good = _make_skill("skgood", "real skill", correct_hash=True)
        stale = _make_skill("skbad", "test fixture", correct_hash=False)
        self._write_skills(skills_file, [good, stale])

        cleanup_workspace_skills(skills_path=skills_file)

        remaining = [json.loads(l) for l in skills_file.read_text().splitlines() if l.strip()]
        assert len(remaining) == 1
        assert remaining[0]["id"] == "skgood"
        captured = capsys.readouterr()
        assert "stale" in captured.out.lower()
        assert "skbad" in captured.out

    def test_duplicates_deduped_after_stale_removal(self, tmp_path, capsys):
        skills_file = tmp_path / "skills.jsonl"
        # Two copies of the same good skill (same content_hash)
        dup1 = _make_skill("sk-a", "skill alpha", correct_hash=True)
        dup2 = dict(dup1)
        dup2["id"] = "sk-b"
        dup2["use_count"] = 5  # higher score → should be kept
        stale = _make_skill("sk-c", "test fixture", correct_hash=False)
        self._write_skills(skills_file, [dup1, dup2, stale])

        cleanup_workspace_skills(skills_path=skills_file)

        remaining = [json.loads(l) for l in skills_file.read_text().splitlines() if l.strip()]
        assert len(remaining) == 1
        assert remaining[0]["id"] == "sk-b"  # higher score kept

    def test_no_stale_no_dups_reports_clean(self, tmp_path, capsys):
        skills_file = tmp_path / "skills.jsonl"
        good = _make_skill("skgood", "real skill", correct_hash=True)
        self._write_skills(skills_file, [good])

        cleanup_workspace_skills(skills_path=skills_file)

        captured = capsys.readouterr()
        assert "no stale" in captured.out.lower()
        assert "no duplicates" in captured.out.lower()


class TestTheSkillsCleanupSurvivesATornByte:
    """A repair verb must not destroy what it was never asked to remove.

    Probed live 2026-08-20 before the fix (destructive-rewrite sweep triage):
    one non-UTF-8 byte crashed the whole verb with UnicodeDecodeError, and a
    truncated row — the shape a crashed append actually leaves — was DELETED
    by the rewrite while the closing summary reported "0 total" removed.
    """

    def _write_raw(self, path: Path, chunks: "list[bytes]") -> None:
        path.write_bytes(b"".join(c + b"\n" for c in chunks))

    def test_a_torn_byte_no_longer_crashes_the_verb(self, tmp_path, capsys):
        skills_file = tmp_path / "skills.jsonl"
        good = json.dumps(_make_skill("skgood", "real skill", correct_hash=True)).encode()
        torn = b'{"id": "sktorn", "name": "torn\xff", "content_hash": "zzz"}'
        self._write_raw(skills_file, [good, torn])

        cleanup_workspace_skills(skills_path=skills_file)  # used to raise

        assert torn in skills_file.read_bytes()

    def test_an_unparseable_row_is_kept_not_deleted(self, tmp_path, capsys):
        skills_file = tmp_path / "skills.jsonl"
        good = json.dumps(_make_skill("skgood", "real skill", correct_hash=True)).encode()
        truncated = b'{"id": "skhalf", "name": "half-writ'
        self._write_raw(skills_file, [good, truncated])

        cleanup_workspace_skills(skills_path=skills_file)

        after = skills_file.read_bytes()
        assert truncated in after, "a row the verb cannot read must ride the rewrite"
        assert good in after

    def test_the_kept_rows_are_announced_to_the_operator(self, tmp_path, capsys):
        skills_file = tmp_path / "skills.jsonl"
        good = json.dumps(_make_skill("skgood", "real skill", correct_hash=True)).encode()
        self._write_raw(skills_file, [good, b'{"id": "skhalf", "name": "half'])

        cleanup_workspace_skills(skills_path=skills_file)

        out = capsys.readouterr().out.lower()
        assert "kept in place" in out
        assert "not removed" in out or "cannot read" in out

    def test_deliberate_removals_still_remove(self, tmp_path, capsys):
        """The strand-and-carry path must not turn the verb into a no-op."""
        skills_file = tmp_path / "skills.jsonl"
        good = json.dumps(_make_skill("skgood", "real skill", correct_hash=True)).encode()
        stale = json.dumps(_make_skill("skbad", "test fixture", correct_hash=False)).encode()
        self._write_raw(skills_file, [good, stale, b'{"id": "skhalf", "name": "half'])

        cleanup_workspace_skills(skills_path=skills_file)

        after = skills_file.read_bytes()
        assert stale not in after, "stale-hash skills are still removed"
        assert b"skhalf" in after and good in after

    def test_a_tainted_twin_is_never_relaundered(self, tmp_path, capsys):
        """A byte-tainted row must not come back as clean \\udcXX escapes."""
        skills_file = tmp_path / "skills.jsonl"
        good = json.dumps(_make_skill("skgood", "real skill", correct_hash=True)).encode()
        tainted = json.dumps(
            _make_skill("sktwin", "tainted skill", correct_hash=True)
        ).encode().replace(b"tainted", b"tain\xffed")
        self._write_raw(skills_file, [good, tainted])

        cleanup_workspace_skills(skills_path=skills_file)

        after = skills_file.read_bytes()
        assert tainted in after
        assert b"udcff" not in after.lower()

    def test_the_default_path_follows_the_workspace_override(self, tmp_path, monkeypatch):
        """doctor must clean the store the running system uses, not ~/.maro."""
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        from doctor import _workspace_skills_path

        resolved = _workspace_skills_path()
        assert str(resolved).startswith(str(tmp_path)), resolved
        assert resolved.name == "skills.jsonl"


class TestTheCleanupVerbHoldsItsLock:
    """Adversarial round 2026-08-20, 5/5 consensus HIGH.

    The first version of the byte-safety fix took the lock only around the
    WRITE, so a save_skill() landing between the snapshot and the lock was
    overwritten by the stale snapshot — a lost update in a verb whose whole
    job is repair.

    This test must fork a real subprocess. locked_write is REENTRANT, so an
    in-process writer acquires the lock the cleanup already holds and its
    append is overwritten no matter how correct the code is — an in-process
    probe of this cannot fail and would be worse than no test.
    """

    def test_a_concurrent_save_from_another_process_is_not_lost(self, tmp_path, capsys):
        import subprocess
        import sys as _sys
        import time

        import jsonl_utils

        src = str(Path(__file__).parent.parent / "src")
        skills_file = tmp_path / "skills.jsonl"
        marker = tmp_path / "child-state"
        first = _make_skill("skfirst", "already here", correct_hash=True)
        skills_file.write_text(json.dumps(first) + "\n", encoding="utf-8")

        arrival = json.dumps(_make_skill("sklate", "arrived mid-cleanup", correct_hash=True))
        # The child HANDSHAKES: it proves the lock is genuinely held by
        # someone else before it appends, and records which. A fixed sleep
        # would let the child append after cleanup finished, so both rows
        # survive and the test passes with no lock at all — adversarial r2
        # (2026-08-20) demonstrated exactly that against the lock-removal
        # mutant. `blocked` is the only outcome that proves serialization.
        writer = (
            f"import sys, time, fcntl\n"
            f"sys.path.insert(0, {src!r})\n"
            "from pathlib import Path\n"
            f"lock = Path({str(skills_file) + '.lock'!r})\n"
            "state = 'never-contended'\n"
            "deadline = time.time() + 20\n"
            "while time.time() < deadline:\n"
            "    try:\n"
            "        fh = open(lock, 'a+')\n"
            "        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)\n"
            "        fh.close()\n"
            "        time.sleep(0.02)\n"
            "    except OSError:\n"
            "        state = 'blocked'\n"
            "        break\n"
            f"Path({str(marker)!r}).write_text(state)\n"
            "from file_lock import locked_append\n"
            f"locked_append(Path({str(skills_file)!r}), {arrival!r})\n"
        )

        real_store_text = jsonl_utils.store_text
        started = {}

        def racing(path):
            text = real_store_text(path)
            started["proc"] = subprocess.Popen([_sys.executable, "-c", writer])
            deadline = time.time() + 25
            while time.time() < deadline and not marker.exists():
                time.sleep(0.05)
            started["state"] = marker.read_text() if marker.exists() else "no-handshake"
            return text

        jsonl_utils.store_text = racing
        try:
            cleanup_workspace_skills(skills_path=skills_file)
        finally:
            jsonl_utils.store_text = real_store_text
            if "proc" in started:
                started["proc"].wait(timeout=30)

        assert started.get("state") == "blocked", (
            "the child never contended for the lock, so this test proves "
            f"nothing about serialization (state={started.get('state')!r})")
        names = [json.loads(l)["name"] for l in skills_file.read_text().splitlines() if l.strip()]
        assert "arrived mid-cleanup" in names, (
            "a save from another process was overwritten by cleanup's snapshot")
        assert "already here" in names


class TestTheCleanupVerbRemovesRowsNotIds:
    def test_a_healthy_skill_sharing_an_id_with_a_stale_one_survives(self, tmp_path, capsys):
        """Adversarial round 2026-08-20 (Minimalist, verified): filtering by
        id removed EVERY row carrying a stale row's id, and reported only the
        stale one. Probed: 2 rows in, 0 left, "1 removed"."""
        skills_file = tmp_path / "skills.jsonl"
        stale = _make_skill("same", "stale copy", correct_hash=False)
        healthy = _make_skill("same", "healthy copy", correct_hash=True)
        skills_file.write_text(json.dumps(stale) + "\n" + json.dumps(healthy) + "\n",
                               encoding="utf-8")

        cleanup_workspace_skills(skills_path=skills_file)

        names = [json.loads(l)["name"] for l in skills_file.read_text().splitlines() if l.strip()]
        assert names == ["healthy copy"], names
        assert "1 stale-hash" in capsys.readouterr().out

    def test_a_json_value_that_is_not_an_object_does_not_crash_the_verb(self, tmp_path, capsys):
        """`[]`, `null` and `"x"` are valid JSON but not rows."""
        skills_file = tmp_path / "skills.jsonl"
        good = json.dumps(_make_skill("skgood", "real skill", correct_hash=True))
        skills_file.write_bytes((good + "\n[]\nnull\n\"x\"\n").encode())

        cleanup_workspace_skills(skills_path=skills_file)  # used to raise

        after = skills_file.read_text()
        assert "[]" in after and "null" in after, "non-row JSON must be carried, not dropped"
        assert good in after

    def test_a_padded_row_is_carried_byte_for_byte(self, tmp_path, capsys):
        """'Verbatim' must mean verbatim — the first fix stripped the line."""
        skills_file = tmp_path / "skills.jsonl"
        good = json.dumps(_make_skill("skgood", "real skill", correct_hash=True))
        padded = '   {"id": "skhalf", "name": "half   '
        skills_file.write_bytes((good + "\n" + padded + "\n").encode())

        cleanup_workspace_skills(skills_path=skills_file)

        assert padded in skills_file.read_text()


class TestTheCleanupVerbValidatesRowsNotJustJson:
    """Adversarial r2 (2026-08-20, Expert QA, verified): the shape boundary
    accepted every dict, but a dict is not yet a Skill. `_skill_hash_is_stale`
    returns "not stale" for anything it cannot build, so a row that is merely
    an object could carry a healthy skill's content_hash plus a higher score,
    win the dedup, and DELETE the healthy row — confident destructive output
    derived from garbage, with no warning."""

    def test_a_dict_that_is_not_a_skill_cannot_win_dedup(self, tmp_path, capsys):
        from skill_types import compute_skill_hash, dict_to_skill

        healthy = _make_skill("skgood", "real skill", correct_hash=True)
        forged = {"id": "forged", "name": "forged",
                  "content_hash": healthy["content_hash"],
                  "created_at": "2030-01-01T00:00:00+00:00",
                  "success_rate": 1.0, "use_count": 999}
        skills_file = tmp_path / "skills.jsonl"
        skills_file.write_text(json.dumps(healthy) + "\n" + json.dumps(forged) + "\n",
                               encoding="utf-8")

        cleanup_workspace_skills(skills_path=skills_file)

        rows = [json.loads(l) for l in skills_file.read_text().splitlines() if l.strip()]
        ids = [r["id"] for r in rows]
        assert "skgood" in ids, "a healthy skill was deleted by a row that is not a Skill"
        assert "forged" in ids, "and the unreadable row is carried, not dropped"
        assert "kept as-is" in capsys.readouterr().out.lower()
