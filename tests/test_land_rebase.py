"""land.sh auto-rebase: temp-worktree replay on a lost landing race.

2026-08-16 (Jeremy): the manual lost-race recipe (worktree add →
cherry-pick → land the sha → remove worktree → converge → triage) now
lives inside land.sh; conflicts abort to manual with the recipe printed,
and --no-rebase restores the old refuse-outright behavior.

Probed against throwaway file:// repos under tmp_path — these tests
never touch this repo's origin. --skip-checks keeps the gate and the
red-main gh query out of the loop (they are not what's under test and
the gh query would hit the real GitHub repo).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LAND = _REPO_ROOT / "scripts" / "land.sh"
_TRIAGE = _REPO_ROOT / "scripts" / "tree-triage.sh"


def _run(cwd, *cmd, check=True):
    res = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise AssertionError(
            f"{cmd} failed ({res.returncode}):\n{res.stdout}\n{res.stderr}")
    return res


def _git(cwd, *args, check=True):
    return _run(cwd, "git", *args, check=check)


def _commit_file(clone, name, content, msg):
    (clone / name).write_text(content)
    _git(clone, "add", name)
    _git(clone, "commit", "-q", "-m", msg)


def _land(clone, *flags):
    return _run(clone, "bash", str(_LAND), "--skip-checks", *flags,
                check=False)


def _head(clone):
    return _git(clone, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def race(tmp_path):
    """Bare origin, clone A (the race winner), clone B (lands second)."""
    origin = tmp_path / "origin.git"
    _run(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    a = tmp_path / "a"
    _run(tmp_path, "git", "clone", "-q", str(origin), str(a))
    _git(a, "config", "user.email", "a@test.local")
    _git(a, "config", "user.name", "session-a")
    _commit_file(a, "base.txt", "line1\n", "seed")
    _git(a, "push", "-q", "origin", "main")
    b = tmp_path / "b"
    _run(tmp_path, "git", "clone", "-q", str(origin), str(b))
    _git(b, "config", "user.email", "b@test.local")
    _git(b, "config", "user.name", "session-b")
    return origin, a, b


def _win_race(a, name="winner.txt", content="from a\n", msg="winner"):
    _commit_file(a, name, content, msg)
    _git(a, "push", "-q", "origin", "main")


class TestFastForwardUnchanged:
    def test_ff_land_still_plain(self, race):
        """Behavior-preservation pin: no race → no rebase machinery."""
        _origin, _a, b = race
        _commit_file(b, "mine.txt", "from b\n", "mine")
        res = _land(b)
        assert res.returncode == 0, res.stderr
        assert "landed:" in res.stdout
        assert "auto-rebase:" not in res.stdout
        assert _git(b, "rev-parse", "origin/main").stdout.strip() == _head(b)

    def test_no_rebase_flag_refuses(self, race):
        """--no-rebase restores the old refusal on a lost race."""
        _origin, a, b = race
        _win_race(a)
        _commit_file(b, "mine.txt", "from b\n", "mine")
        res = _land(b, "--no-rebase")
        assert res.returncode == 1
        assert "has diverged" in res.stderr
        # Nothing pushed: origin/main is still the winner commit.
        assert (_git(b, "rev-parse", "origin/main").stdout.strip()
                == _head(a))


class TestAutoRebase:
    def test_lost_race_lands_via_replay(self, race):
        """The core: disjoint concurrent work lands with one command —
        replayed onto fresh origin/main in a temp worktree, pushed
        ff-only, caller ref converged, no worktree leaked."""
        _origin, a, b = race
        _win_race(a)
        _commit_file(b, "mine.txt", "from b\n", "mine")
        res = _land(b)
        assert res.returncode == 0, res.stderr
        assert "auto-rebase:" in res.stdout
        assert "clean replay" in res.stdout
        assert "converged:" in res.stdout
        # Linear history containing both sides, B's commit on top.
        subjects = _git(b, "log", "--format=%s",
                        "origin/main").stdout.split()
        assert subjects == ["mine", "winner", "seed"]
        # HEAD-landing converge: local ref == origin/main == landed sha.
        assert _head(b) == _git(b, "rev-parse",
                                "origin/main").stdout.strip()
        # No leaked worktree.
        wt = _git(b, "worktree", "list").stdout.strip().splitlines()
        assert len(wt) == 1
        # Without tree-triage present, the upstream-touched path honestly
        # shows stale (deleted vs index) and the note says so.
        assert "tree-triage.sh not found" in res.stderr
        assert " D winner.txt" in _git(b, "status",
                                       "--porcelain").stdout

    def test_triage_materializes_upstream_paths(self, race):
        """With tree-triage.sh present, the replayed-over upstream paths
        are restored — the full manual dance, automated."""
        _origin, a, b = race
        _win_race(a)
        (b / "scripts").mkdir()
        shutil.copy(_TRIAGE, b / "scripts" / "tree-triage.sh")
        (b / "scripts" / "tree-triage.sh").chmod(0o755)
        _commit_file(b, "mine.txt", "from b\n", "mine")
        res = _land(b)
        assert res.returncode == 0, res.stderr
        assert (b / "winner.txt").read_text() == "from a\n"
        status = _git(b, "status", "--porcelain").stdout
        assert " D " not in status and " M " not in status

    def test_shared_file_automerge_materializes(self, race):
        """First-live-fire pin (2026-08-16, GOAL_BRAIN.md): both sessions
        edit ONE shared file in auto-mergeable regions. Post-converge the
        working copy matches the PRE-replay commit — no longer an
        ancestor, so tree-triage calls it REAL — and the ORIG_SHA blob
        compare must materialize the merged version instead of leaving a
        manual chore on exactly the files every session touches."""
        _origin, a, b = race
        shared = "journal.txt"
        base = "".join(f"line{i}\n" for i in range(1, 21))
        _commit_file(a, shared, base, "shared-base")
        _git(a, "push", "-q", "origin", "main")
        _git(b, "pull", "-q", "origin", "main")
        # A edits the top, B edits the bottom — disjoint hunks, clean merge.
        _win_race(a, name=shared,
                  content=base.replace("line1\n", "A-entry\n"),
                  msg="a-journal")
        _commit_file(b, shared,
                     base.replace("line20\n", "B-entry\n"), "b-journal")
        (b / "scripts").mkdir()
        shutil.copy(_TRIAGE, b / "scripts" / "tree-triage.sh")
        (b / "scripts" / "tree-triage.sh").chmod(0o755)
        res = _land(b)
        assert res.returncode == 0, res.stderr
        assert f"materialized: {shared}" in res.stdout
        merged = (b / shared).read_text()
        assert "A-entry" in merged and "B-entry" in merged
        status = _git(b, "status", "--porcelain").stdout
        assert shared not in status

    def test_conflict_aborts_to_manual(self, race):
        """Conflicting replay: no push, no caller mutation, no leaked
        worktree, and the manual recipe on stderr."""
        _origin, a, b = race
        _win_race(a, name="base.txt", content="A version\n",
                  msg="winner-conflict")
        _commit_file(b, "base.txt", "B version\n", "mine-conflict")
        before = _head(b)
        res = _land(b)
        assert res.returncode == 1
        assert "auto-rebase hit conflicts" in res.stderr
        assert "cherry-pick" in res.stderr
        # Origin untouched by B; B's checkout untouched; nothing mid-pick.
        assert (_git(b, "rev-parse", "origin/main").stdout.strip()
                == _head(a))
        assert _head(b) == before
        assert _git(b, "status", "--porcelain").stdout == ""
        assert len(_git(b, "worktree",
                        "list").stdout.strip().splitlines()) == 1

    def test_strictly_behind_is_nothing_to_land(self, race):
        """A ref whose commits are all contained in origin/main exits 0
        with an honest message instead of replaying an empty range."""
        _origin, a, b = race
        _win_race(a)
        res = _land(b)  # B has no commits of its own; HEAD == seed
        assert res.returncode == 0, res.stderr
        assert "already contained" in res.stdout

    def test_merge_commits_refused(self, race):
        """cherry-pick can't replay merges; the script says so instead of
        guessing mainline parents."""
        _origin, a, b = race
        _win_race(a)
        _git(b, "checkout", "-q", "-b", "side")
        _commit_file(b, "side.txt", "side\n", "side-work")
        _git(b, "checkout", "-q", "main")
        _git(b, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        res = _land(b)
        assert res.returncode == 1
        assert "merge commits" in res.stderr
