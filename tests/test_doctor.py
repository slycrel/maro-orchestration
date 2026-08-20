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
        # Identical in every field that says what the skill DOES; newer, so
        # it is the one kept. This used to bump `use_count` instead, which
        # after adversarial r5 makes the two rows different skills (a
        # diverged use count is evidence, and deleting it destroys it) —
        # see TestOnlyFieldsThatCannotChangeBehaviourAreIgnoredByDedup.
        dup2["id"] = "sk-b"
        dup2["created_at"] = "2030-01-01T00:00:00Z"
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


class TestARowMustBeProvenASkillNotJustADict:
    """Adversarial r3 (2026-08-20, 5/5 consensus HIGH — the r2 fix's own
    regression): r2 answered "a dict is not yet a Skill" with
    `dict_to_skill(row)`, which is a CONSTRUCTOR. Python does not enforce
    dataclass annotations, so `description=7` sails through it, the hash
    cannot be recomputed, `_skill_hash_is_stale` answers "not stale" for the
    failure, and the forgery still wins the dedup and DELETES the healthy
    skill. Probed against the r2 code: 2 rows in, only `forged` out."""

    def _pair(self, tmp_path, **forged_overrides):
        from skill_types import compute_skill_hash, dict_to_skill

        healthy = _make_skill("skgood", "real skill", correct_hash=True)
        forged = dict(healthy)
        forged.update(id="forged", name="forged",
                      created_at="2030-01-01T00:00:00+00:00",
                      use_count=999, success_rate=1.0)
        forged.update(forged_overrides)
        # the forgery DECLARES the healthy row's hash, so it groups with it
        forged["content_hash"] = healthy["content_hash"]
        f = tmp_path / "skills.jsonl"
        f.write_text(json.dumps(healthy) + "\n" + json.dumps(forged) + "\n",
                     encoding="utf-8")
        return f

    def test_a_content_field_that_is_not_text_cannot_evict_a_healthy_row(
            self, tmp_path, capsys):
        f = self._pair(tmp_path, description=7)

        cleanup_workspace_skills(skills_path=f)

        ids = [json.loads(l)["id"] for l in f.read_text().splitlines() if l.strip()]
        assert "skgood" in ids, "a healthy skill was deleted by a row that is not a Skill"
        assert "forged" in ids, "and the row we could not read is carried, not dropped"
        assert "kept as-is" in capsys.readouterr().out.lower()

    def test_a_nan_score_cannot_win_a_dedup(self, tmp_path, capsys):
        """`max()` ordering with NaN is undefined, so a NaN success_rate wins
        or loses by position. A row whose ranking fields cannot be compared
        must not be ranked at all."""
        f = self._pair(tmp_path, description="test description",
                       success_rate=float("nan"))
        raw = f.read_text().replace("NaN", "NaN")  # json.dumps emits bare NaN
        f.write_text(raw, encoding="utf-8")

        cleanup_workspace_skills(skills_path=f)

        ids = [l for l in f.read_text().splitlines() if l.strip()]
        assert any('"skgood"' in l for l in ids), "the healthy row lost to a NaN"
        assert any('"forged"' in l for l in ids), "the NaN row was destroyed"

    def test_a_list_field_that_is_a_string_cannot_evict_a_healthy_row(
            self, tmp_path, capsys):
        f = self._pair(tmp_path, description="test description",
                       trigger_patterns="not-a-list")

        cleanup_workspace_skills(skills_path=f)

        ids = [json.loads(l)["id"] for l in f.read_text().splitlines() if l.strip()]
        assert "skgood" in ids
        assert "forged" in ids

    def test_every_row_in_the_live_shape_still_validates(self):
        """The negative control: a guard that strands everything is not a
        guard, it is an outage. This is the shape all 423 rows in the live
        store carry."""
        from skill_types import validate_skill_row

        assert validate_skill_row(_make_skill("sk", "n", correct_hash=True)).id == "sk"


class TestDedupDeletesOnlyProvableRedundancy:
    """Adversarial r4 (2026-08-20, 5/5 consensus HIGH). r3 answered "validate
    the row" and five lenses each found a different field to smuggle junk
    through — `tier` as a list, `failure_notes` carrying an int, `tags` as a
    string, an empty `id` with `created_at="zzzz"` winning a lexical compare,
    an empty `content_hash` falling through to an ID-keyed dedup the comment
    claimed did not exist. Five shapes of one bug is a whack-a-mole, not a
    fix.

    The root cause was the dedup KEY: grouping by the row's declared
    `content_hash` asks a row to nominate its own peers, and that hash covers
    only name + description + steps_template + objective. So a row could
    match on the hash, change `trigger_patterns` — which decides what the
    skill MATCHES — and evict the healthy row for being its duplicate. Now
    two rows are duplicates only when they agree on everything except
    bookkeeping.
    """

    def _pair(self, tmp_path, **forged):
        base = _make_skill("skgood", "real skill", correct_hash=True)
        base["trigger_patterns"] = ["safe"]
        from skill_types import compute_skill_hash, dict_to_skill
        base["content_hash"] = compute_skill_hash(dict_to_skill(base))
        row = dict(base)
        # Only id and created_at differ. r5 tightened the identity to exactly
        # those two plus the derived hash, so a `use_count` bump — which the
        # r4 version of this helper used — now means "not a duplicate", and a
        # control built on it could never delete anything.
        row.update(id="forged", created_at="2030-01-01T00:00:00+00:00")
        row.update(forged)
        f = tmp_path / "skills.jsonl"
        f.write_text(json.dumps(base) + "\n" + json.dumps(row) + "\n",
                     encoding="utf-8")
        return f

    @pytest.mark.parametrize("label,forged", [
        ("trigger_patterns differ, both well-typed", {"trigger_patterns": ["destroy prod"]}),
        ("tier is a list", {"tier": ["not-a-tier"]}),
        ("failure_notes carries an int", {"failure_notes": [7]}),
        ("tags is a string", {"tags": "not-a-list"}),
        ("empty id and a lexical-max timestamp", {"id": "", "created_at": "zzzz"}),
    ])
    def test_a_foreign_row_cannot_evict_a_healthy_one(self, tmp_path, label, forged):
        f = self._pair(tmp_path, **forged)

        cleanup_workspace_skills(skills_path=f)

        rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        assert any(r.get("id") == "skgood" for r in rows), (
            f"the healthy skill was deleted — {label}")
        assert len(rows) == 2, f"the foreign row was destroyed — {label}"

    def test_two_unrelated_rows_sharing_an_id_are_not_deduped_by_it(self, tmp_path):
        """The old fallback grouped hash-less rows by `id` under a comment
        that said "can't dedup without a key". It deduped by that key, so two
        unrelated skills that shared an id were one delete away from each
        other. There is no fallback key now — and a row with no hash cannot
        be verified, so it is stranded rather than ranked."""
        a = _make_skill("dup-id", "skill A", correct_hash=True)
        b = _make_skill("dup-id", "skill B — different work", correct_hash=True)
        a["content_hash"] = b["content_hash"] = ""
        f = tmp_path / "skills.jsonl"
        f.write_text(json.dumps(a) + "\n" + json.dumps(b) + "\n", encoding="utf-8")

        cleanup_workspace_skills(skills_path=f)

        names = [json.loads(l)["name"] for l in f.read_text().splitlines() if l.strip()]
        assert sorted(names) == ["skill A", "skill B — different work"]

    def test_a_true_duplicate_is_still_removed(self, tmp_path, capsys):
        """The negative control. A verb that never deletes anything is not a
        cleanup verb — the deliberate drop must still happen."""
        f = self._pair(tmp_path)      # differs only in bookkeeping

        cleanup_workspace_skills(skills_path=f)

        rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        assert len(rows) == 1 and rows[0]["id"] == "forged"
        assert "identical copies" in capsys.readouterr().out


class TestTheValidatorAcceptsEveryShapeTheLiveStoreCarries:
    """Adversarial r4 (2 lenses, LOW but fair): the r3 negative control
    claimed "all 423 rows in the live store validate" and then validated one
    synthetic fixture whose `variant_of` is always None. The live store has
    20 rows with a string `variant_of`, 43 with non-empty `failure_notes`, 27
    with non-empty `tags`. A validator tightening could strand those and this
    test would not notice. These are the observed shapes, checked in."""

    SHAPES = [
        {},
        {"variant_of": "sk-parent", "variant_wins": 3, "variant_losses": 1},
        {"failure_notes": ["timed out", "wrong tool"]},
        {"tags": ["research", "ops"]},
        {"source_loop_ids": ["loop-1", "loop-2"]},
        {"steps_template": []},
        {"imported": {"pack": "maro-pack-1", "at": "2026-01-01T00:00:00+00:00"}},
        {"tier": "graduated", "circuit_state": "open", "domain": "ops",
         "island": "a", "project": "p", "origin": "imported"},
    ]

    @pytest.mark.parametrize("over", SHAPES)
    def test_it_validates(self, over):
        from skill_types import validate_skill_row

        row = _make_skill("sk", "n", correct_hash=True)
        row.update(over)
        validate_skill_row(row)


class TestAnUnprovableRowIsStrandedNotAdmitted:
    """The dedup-identity fix (r4) means a junk row can no longer evict a
    healthy one even if it IS admitted — which is the point, but it also made
    every "healthy row survives" test pass with the validator's field checks
    removed. The mutation sweep said so: four validator mutants survived.
    That is a hole in the suite, not a reason to drop the checks, because
    admission has its own consequence — an admitted row is re-serialized into
    the rewrite, a stranded one rides through byte for byte. These pin the
    boundary directly."""

    @pytest.mark.parametrize("label,over", [
        ("description is not text", {"description": 7}),
        # The one shape ONLY the hash call catches. Every field it hashes is
        # also in _STR_FIELDS, so a non-str is rejected either way — but a
        # lone surrogate IS a str, passes every isinstance check, and dies on
        # .encode("utf-8"). Probed: without compute_skill_hash the row is
        # admitted. (loads_clean refuses taint upstream on the doctor path;
        # this is the boundary function's own contract, for other callers.)
        ("description carries a lone surrogate", {"description": "bad\udcff"}),
        ("name carries a lone surrogate", {"name": "n\udc80"}),
        ("a step carries a lone surrogate", {"steps_template": ["ok", "x\udcff"]}),
        ("tier is a list", {"tier": ["x"]}),
        ("circuit_state is an int", {"circuit_state": 3}),
        ("domain is a dict", {"domain": {}}),
        ("failure_notes carries an int", {"failure_notes": [7]}),
        ("trigger_patterns is a string", {"trigger_patterns": "x"}),
        # NOT here: `tags` — normalize_tags() coerces a bare string to
        # ["x"] on purpose (a stored string must not iterate into
        # character tags). Coercion is the contract, not a hole.
        ("id is empty", {"id": ""}),
        ("content_hash is empty", {"content_hash": ""}),
        ("name is whitespace", {"name": "   "}),
        ("created_at is not a timestamp", {"created_at": "zzzz"}),
        ("created_at is empty", {"created_at": ""}),
        ("success_rate is NaN", {"success_rate": float("nan")}),
        ("use_count is a string", {"use_count": "9"}),
        ("use_count is a bool", {"use_count": True}),
        ("variant_of is a list", {"variant_of": []}),
        ("imported is a string", {"imported": "yes"}),
    ])
    def test_the_validator_refuses_it(self, label, over):
        from skill_types import validate_skill_row

        row = _make_skill("sk", "n", correct_hash=True)
        row.update(over)
        with pytest.raises((TypeError, ValueError, KeyError)):
            validate_skill_row(row)

    def test_a_refused_row_rides_the_rewrite_byte_for_byte(self, tmp_path, capsys):
        """The observable difference between stranded and admitted: an
        admitted row is re-serialized by `json.dumps`, a stranded one is the
        original bytes. Distinctive spacing makes that visible."""
        healthy = _make_skill("skgood", "real skill", correct_hash=True)
        junk = dict(_make_skill("junk", "junk", correct_hash=True), tier=["nope"])
        raw = "   " + json.dumps(junk, separators=(" , ", " : ")) + "   "
        f = tmp_path / "skills.jsonl"
        f.write_text(json.dumps(healthy) + "\n" + raw + "\n", encoding="utf-8")

        cleanup_workspace_skills(skills_path=f)

        assert raw in f.read_text(), "a row the verb could not prove was re-serialized"
        assert "kept as-is" in capsys.readouterr().out.lower()


class TestOnlyFieldsThatCannotChangeBehaviourAreIgnoredByDedup:
    """Adversarial r5 (5/5 HIGH) on the r4 dedup identity: r4 named a dozen
    fields "bookkeeping" and excluded them from the comparison, so rows that
    a consumer treats differently still counted as "identical copies" and one
    got deleted. `circuit_state` is the proof — an open circuit EXCLUDES a
    skill from matching — but the lesson is the shape: an exclusion list over
    an open field space fails open on every field nobody thought about."""

    @pytest.mark.parametrize("field,a,b", [
        ("circuit_state", "closed", "open"),
        ("failure_notes", [], ["it broke"]),
        ("use_count", 0, 41),
        ("success_rate", 1.0, 0.1),
        ("utility_score", 1.0, 9.0),
        ("consecutive_failures", 0, 3),
        ("variant_wins", 0, 5),
        ("source_loop_ids", [], ["loop-7"]),
        ("imported", {}, {"pack": "x"}),
    ])
    def test_rows_differing_there_are_not_the_same_skill(self, field, a, b):
        from doctor import _dedup_identity

        left = dict(_make_skill("l", "n", correct_hash=True), **{field: a})
        right = dict(_make_skill("r", "n", correct_hash=True), **{field: b})
        assert _dedup_identity(left) != _dedup_identity(right)

    def test_the_three_that_are_ignored_stay_ignored(self):
        """id, the derived hash, and the tiebreaker timestamp — the fields two
        rows must differ on to be two rows at all."""
        from doctor import _dedup_identity

        left = _make_skill("l", "n", correct_hash=True)
        right = dict(_make_skill("r", "n", correct_hash=True),
                     content_hash="deadbeef", created_at="2030-01-01T00:00:00")
        assert _dedup_identity(left) == _dedup_identity(right)

    def test_an_open_circuit_twin_does_not_evict_the_usable_skill(self, tmp_path, capsys):
        """The literal end-to-end path: cleanup must keep both."""
        healthy = dict(_make_skill("healthy", "s", correct_hash=True),
                       circuit_state="closed")
        forged = dict(_make_skill("forged", "s", correct_hash=True),
                      circuit_state="open", created_at="2030-01-01T00:00:00")
        f = tmp_path / "skills.jsonl"
        f.write_text(json.dumps(healthy) + "\n" + json.dumps(forged) + "\n",
                     encoding="utf-8")

        cleanup_workspace_skills(skills_path=f)

        ids = {json.loads(ln)["id"] for ln in f.read_text().splitlines() if ln.strip()}
        assert ids == {"healthy", "forged"}, "a behaviour difference was deduped away"


class TestTheValidatorProvesTheStoredValueNotTheCoercedOne:
    """Adversarial r5 (2 lenses, probed): every check read `getattr(skill, …)`
    AFTER `dict_to_skill`, which coerces with int()/float()/normalize_tags —
    so a stored `"7"` was proven to be an int. Those same fields are the ones
    dedup ignores, so the forged row then won on created_at."""

    @pytest.mark.parametrize("field,stored", [
        ("consecutive_failures", "7"),
        ("consecutive_successes", "7"),
        ("variant_wins", "7"),
        ("variant_losses", "7"),
        ("utility_score", True),
        ("utility_score", "1.0"),
        ("tags", "not-a-list"),
    ])
    def test_a_coercible_junk_value_is_refused_not_converted(self, field, stored):
        from skill_types import validate_skill_row

        row = dict(_make_skill("sk", "n", correct_hash=True), **{field: stored})
        with pytest.raises((TypeError, ValueError)):
            validate_skill_row(row)

    def test_every_live_shape_still_validates_under_the_raw_checks(self):
        """A validator that strands everything is an outage, not a guard.
        Re-probed against the live store after the r5 raw-value change:
        423 rows, 0 stranded."""
        from skill_types import validate_skill_row

        for over in TestTheValidatorAcceptsEveryShapeTheLiveStoreCarries.SHAPES:
            row = _make_skill("sk", "n", correct_hash=True)
            row.update(over)
            validate_skill_row(row)


class TestAbsenceIsNotADefaultForTheFieldsThisVerbActsOn:
    """Adversarial r6 (2026-08-20, 4 lenses, probed) on the r5 fix layer.
    r5's move to raw-value checks was right and dropped a guard on the way:
    `if name in d` means an ABSENT field is fine, and for these two that is
    not a default, it is an absence of proof. A missing `content_hash` makes
    the stale check answer "not stale" (nothing to compare); `created_at` is
    the tiebreaker. Both are excluded from the dedup identity, so neither
    absence shows up as a difference — the forged row simply wins."""

    @pytest.mark.parametrize("missing", ["content_hash", "created_at"])
    def test_the_validator_refuses_the_row(self, missing):
        from skill_types import validate_skill_row

        row = _make_skill("sk", "n", correct_hash=True)
        row.pop(missing)
        with pytest.raises(KeyError):
            validate_skill_row(row)

    @pytest.mark.parametrize("missing", ["content_hash", "created_at"])
    def test_a_hashless_later_twin_does_not_evict_the_verified_row(
            self, missing, tmp_path, capsys):
        """The literal end-to-end path r6 probed: both rows must survive."""
        healthy = _make_skill("healthy", "s", correct_hash=True)
        forged = dict(healthy, id="forged", created_at="2030-01-01T00:00:00")
        forged.pop(missing)
        f = tmp_path / "skills.jsonl"
        f.write_text(json.dumps(healthy) + "\n" + json.dumps(forged) + "\n",
                     encoding="utf-8")

        cleanup_workspace_skills(skills_path=f)

        text = f.read_text()
        assert '"healthy"' in text, "the verified row was deleted"
        assert '"forged"' in text, "the unprovable row was destroyed, not stranded"
        assert "kept as-is" in capsys.readouterr().out.lower()


class TestTheTiebreakIsTheInstantNotTheString:
    """Adversarial r6 (Failure Operator, probed): `score_skill` compared
    `created_at` as text. `2026-01-01T00:00:00+14:00` sorts AFTER
    `2025-12-31T23:00:00-12:00` lexically and BEFORE it in real time, so the
    older row was kept and the newer deleted — both rows valid, both
    timestamps legal ISO-8601, and nothing in the output saying which went."""

    def _pair(self, tmp_path, newer_at, older_at):
        newer = dict(_make_skill("newer", "same", correct_hash=True),
                     created_at=newer_at)
        older = dict(_make_skill("older", "same", correct_hash=True),
                     created_at=older_at)
        f = tmp_path / "skills.jsonl"
        f.write_text(json.dumps(newer) + "\n" + json.dumps(older) + "\n",
                     encoding="utf-8")
        return f

    def test_offsets_do_not_reverse_the_order(self, tmp_path):
        f = self._pair(tmp_path, "2025-12-31T23:00:00-12:00",
                       "2026-01-01T00:00:00+14:00")

        cleanup_workspace_skills(skills_path=f)

        kept = [json.loads(l)["id"] for l in f.read_text().splitlines() if l.strip()]
        assert kept == ["newer"], "the lexically-later but older row won"

    def test_the_ordinary_case_still_keeps_the_newer_row(self, tmp_path):
        """Negative control — the fix must not invert the normal ordering."""
        f = self._pair(tmp_path, "2030-01-01T00:00:00+00:00",
                       "2020-01-01T00:00:00+00:00")

        cleanup_workspace_skills(skills_path=f)

        kept = [json.loads(l)["id"] for l in f.read_text().splitlines() if l.strip()]
        assert kept == ["newer"]

    def test_a_group_that_mixes_naive_and_aware_keeps_every_row(
            self, tmp_path, capsys):
        """Both shapes are in the live store, and `max()` over mixed
        awareness raises — so r6 stamped UTC on the naive one and ranked.
        Adversarial r7 (Architect, probed): `replace(tzinfo=utc)` is not a
        conversion, it ASSERTS a fact the row does not carry, and a naive
        value can denote either side of an aware one. The verb cannot prove
        which is newer, so it does not get to delete either."""
        f = self._pair(tmp_path, "2030-01-01T00:00:00", "2020-01-01T00:00:00+00:00")

        cleanup_workspace_skills(skills_path=f)

        kept = [json.loads(l)["id"] for l in f.read_text().splitlines() if l.strip()]
        assert sorted(kept) == ["newer", "older"]
        out = capsys.readouterr().out
        assert "mix offset-aware and naive" in out, out
        assert "0 duplicate(s) removed" in out


class TestARepairVerbThatRemovesNothingChangesNothing:
    """Adversarial r7 (2026-08-20, Skeptic, probed) — the sharpest find of
    the arc after the original three, because nothing was deleted. r6's
    stricter validator strands more rows; the rewrite appended stranded rows
    AFTER the admitted ones; and `skills.load_skills` reads the file in
    reverse letting the LAST row for an id win. So a legacy row sharing an id
    with a verified one was promoted from ignored to live — by a verb that
    printed "0 removed" and "kept in place". Order is part of the data."""

    def _store(self, tmp_path, rows):
        f = tmp_path / "skills.jsonl"
        f.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return f

    def test_a_stranded_row_is_not_promoted_over_a_verified_twin(self, tmp_path):
        from skills import load_skills

        # Both rows carry a hash correct for their OWN content — otherwise
        # the verified row is stale and the stale pass deletes it, which is
        # a different (correct) behaviour and would hide this one.
        legacy = _make_skill("same-id", "legacy name", correct_hash=True)
        legacy.pop("content_hash")            # readable, unprovable, stranded
        verified = _make_skill("same-id", "verified name", correct_hash=True)
        f = self._store(tmp_path, [legacy, verified])

        def winner():
            with patch("skills._skills_path", return_value=f):
                return [(s.id, s.name) for s in load_skills()]

        before = winner()
        cleanup_workspace_skills(skills_path=f)
        assert winner() == before, "a verb that removed nothing changed which skill is live"

    def test_the_file_keeps_the_order_it_was_read_in(self, tmp_path):
        rows = [_make_skill("a", "n"), _make_skill("b", "n2")]
        rows.insert(1, dict(_make_skill("junk", "n3"), created_at="nope"))
        f = self._store(tmp_path, rows)

        cleanup_workspace_skills(skills_path=f)

        ids = [json.loads(l).get("id", "?") if l.startswith("{") else "?"
               for l in f.read_text().splitlines() if l.strip()]
        assert ids == ["a", "junk", "b"], ids


class TestTheOperatorIsToldWhichRowWent:
    """Adversarial r7 (4 lenses): the group line printed a shared hash prefix
    and a shared name — by construction the two things that CANNOT tell the
    rows apart — so an operator could not tell which record the repair verb
    had destroyed, or recover it. The path is part of the result; so is the
    id."""

    def test_both_the_survivor_and_the_removed_row_are_named(self, tmp_path, capsys):
        newer = dict(_make_skill("keeper", "n", correct_hash=True),
                     created_at="2030-01-01T00:00:00+00:00")
        older = dict(_make_skill("goner", "n", correct_hash=True),
                     created_at="2020-01-01T00:00:00+00:00")
        f = tmp_path / "skills.jsonl"
        f.write_text(json.dumps(newer) + "\n" + json.dumps(older) + "\n",
                     encoding="utf-8")

        cleanup_workspace_skills(skills_path=f)

        out = capsys.readouterr().out
        assert "keeping 'keeper'" in out, out
        assert "removing 'goner'" in out, out
        assert "2020-01-01T00:00:00+00:00" in out, "the removed row's timestamp"


class TestAnUnprovableRowIsNotReportedAsCorruption:
    """Adversarial r7 (QA): two different refusals need two different
    repairs. r6's stricter validator made "readable but unprovable" common
    and the summary called every stranded row unparseable/byte-tainted,
    which points the operator at the wrong tool."""

    def test_a_schema_gap_and_a_torn_byte_are_counted_apart(self, tmp_path, capsys):
        good = _make_skill("good", "n", correct_hash=True)
        schema = _make_skill("nohash", "n", correct_hash=True)
        schema.pop("content_hash")
        f = tmp_path / "skills.jsonl"
        f.write_bytes(json.dumps(good).encode() + b"\n"
                      + json.dumps(schema).encode() + b"\n"
                      + b'{"id": "torn\xff"}\n')

        cleanup_workspace_skills(skills_path=f)

        out = capsys.readouterr().out
        assert "1 unparseable/byte-tainted" in out, out
        assert "1 readable but unprovable as a skill" in out, out


class TestTheStoreItRewroteIsNamed:
    """Adversarial r8 (5/5 seats, probed). r7 named the ROWS and never named
    the STORE — while its own comment said "the path is part of the result".
    An operator running cleanup under a MARO_WORKSPACE override, or reading
    an automation log, could see that a record was destroyed and not which
    skills.jsonl lost it. That is the retention decree's own sentence, with
    no executing line behind it until now."""

    def test_every_destructive_announcement_carries_the_store_path(
            self, tmp_path, capsys):
        newer = dict(_make_skill("keeper", "n", correct_hash=True),
                     created_at="2030-01-01T00:00:00+00:00")
        older = dict(_make_skill("goner", "n", correct_hash=True),
                     created_at="2020-01-01T00:00:00+00:00")
        stale = _make_skill("stale", "other", correct_hash=False)
        f = tmp_path / "elsewhere" / "skills.jsonl"
        f.parent.mkdir()
        f.write_bytes("\n".join(json.dumps(r) for r in (newer, older, stale))
                      .encode() + b"\n" + b'{"id": "torn\xff"}\n')

        cleanup_workspace_skills(skills_path=f)

        # Each announcement separately, not a count: the mutation sweep
        # showed that "the path appears at least three times" passes with
        # any ONE of the four lines stripped, which is a test that cannot
        # fail in the direction it was written for.
        lines = capsys.readouterr().out.splitlines()

        def carries(prefix):
            named = [l for l in lines if l.startswith(prefix)]
            assert named, f"no line starts with {prefix!r}:\n" + "\n".join(lines)
            assert all(str(f) in l for l in named), \
                f"{prefix!r} does not name the store:\n" + "\n".join(named)

        carries("Rewriting")                 # before the destructive write
        carries("Unreadable line")           # the row it could not read
        carries("Kept in place")             # the strand summary
        carries("Cleaned")                   # the closing count

    def test_the_stale_branch_names_its_row_as_fully_as_the_duplicate_one(
            self, tmp_path, capsys):
        """The sibling r7 left half-done: `created_at` is the only thing
        that tells two rows sharing an id apart, and this branch DELETES."""
        stale = dict(_make_skill("stale", "n", correct_hash=False),
                     created_at="2019-07-04T00:00:00+00:00")
        f = tmp_path / "skills.jsonl"
        f.write_text(json.dumps(stale) + "\n", encoding="utf-8")

        cleanup_workspace_skills(skills_path=f)

        out = capsys.readouterr().out
        assert "2019-07-04T00:00:00+00:00" in out, out


class TestTheRefusalKindIsRecordedNotGuessedFromTheExceptionName:
    """Adversarial r8 (Minimalist + QA, probed). r7 split "corrupt" from
    "unprovable" by testing the exception's class NAME against three
    strings, which is a denylist — the same shape r5 already wrote a lesson
    about. Probed: a 401-digit `success_rate` is valid JSON with readable
    bytes, is refused with OverflowError, and was reported to the operator
    as byte corruption."""

    def test_a_readable_row_refused_with_an_unlisted_exception(
            self, tmp_path, capsys):
        row = dict(_make_skill("big", "n", correct_hash=True),
                   success_rate=10 ** 401)
        f = tmp_path / "skills.jsonl"
        f.write_text(json.dumps(row) + "\n", encoding="utf-8")

        cleanup_workspace_skills(skills_path=f)

        out = capsys.readouterr().out
        assert "OverflowError" in out, out
        assert "1 readable but unprovable as a skill" in out, out
        assert "unparseable/byte-tainted" not in out, out

    def test_the_per_row_line_says_which_repair_it_needs(
            self, tmp_path, capsys):
        """r7's per-row message called every stranded row "Unreadable",
        including the ones whose bytes and JSON are fine — contradicting the
        summary it prints four lines later."""
        schema = _make_skill("nohash", "n", correct_hash=True)
        schema.pop("content_hash")
        f = tmp_path / "skills.jsonl"
        f.write_bytes(json.dumps(schema).encode() + b"\n"
                      + b'{"id": "torn\xff"}\n')

        cleanup_workspace_skills(skills_path=f)

        lines = capsys.readouterr().out.splitlines()
        readable = [l for l in lines if "not provably a skill" in l]
        unreadable = [l for l in lines if l.startswith("Unreadable line")]
        assert len(readable) == 1, lines
        assert len(unreadable) == 1, lines
        assert "torn" not in readable[0]
