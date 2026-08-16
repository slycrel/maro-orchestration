"""tree-triage.sh classification pins.

The script's contract is two-valued: STALE (this tree is behind a landed
state — restore) vs REAL (another session's uncommitted work — never
touch). The deletion case is the hard one (adversarial review 2026-08-06
R3-5): a file missing from the tree is a `reset --mixed` materialization
gap only when some recent ancestor also lacked the path; if every
ancestor has it, the absence is a deletion in progress and --fix must
leave it alone.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "tree-triage.sh")

_GIT_ENV_ARGS = [
    "-c", "user.email=test@example.com",
    "-c", "user.name=triage-test",
    "-c", "commit.gpgsign=false",
]


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = None
    if date is not None:
        import os
        env = {**os.environ,
               "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    out = subprocess.run(
        ["git", *_GIT_ENV_ARGS, *args],
        cwd=repo, capture_output=True, text=True, check=True, env=env,
    )
    return out.stdout


def _seed_repo(tmp_path: Path) -> Path:
    """Two commits: a.txt lives in both; b.txt only in the second."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("original a\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "add a")
    (repo / "b.txt").write_text("landed b\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "add b")
    return repo


def _triage(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["bash", SCRIPT, *args], cwd=repo,
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def test_missing_recently_added_file_is_stale_and_fix_restores_it(tmp_path):
    repo = _seed_repo(tmp_path)
    # Simulate the reset --mixed gap: b.txt in HEAD+index, absent from tree,
    # and an ancestor (the first commit) predates it.
    (repo / "b.txt").unlink()

    report = _triage(repo)
    assert "b.txt" in report and "STALE" in report

    _triage(repo, "--fix")
    assert (repo / "b.txt").read_text() == "landed b\n"


def test_deleted_long_tracked_file_is_real_and_fix_leaves_it(tmp_path):
    repo = _seed_repo(tmp_path)
    # Every recent ancestor has a.txt — no historical state explains its
    # absence. This is another session's rm; --fix must not resurrect it.
    (repo / "a.txt").unlink()

    report = _triage(repo)
    assert "REAL" in report
    assert "STALE" not in report.split("a.txt")[1].splitlines()[0]

    _triage(repo, "--fix")
    assert not (repo / "a.txt").exists()


def test_staged_deletion_is_real_even_for_recently_added_file(tmp_path):
    repo = _seed_repo(tmp_path)
    # `git rm` records intent in the index; reset --mixed leaves
    # index == HEAD, so a staged deletion can never be a reset artifact.
    _git(repo, "rm", "-q", "b.txt")

    report = _triage(repo)
    assert "REAL uncommitted work (staged)" in report

    _triage(repo, "--fix")
    assert not (repo / "b.txt").exists()


def test_modified_file_matching_ancestor_blob_is_stale(tmp_path):
    repo = _seed_repo(tmp_path)
    (repo / "a.txt").write_text("newer a\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "update a")
    # Tree rolls back to the ancestor's exact content — behind, not ahead.
    (repo / "a.txt").write_text("original a\n")

    report = _triage(repo)
    assert "STALE (matches ancestor" in report

    _triage(repo, "--fix")
    assert (repo / "a.txt").read_text() == "newer a\n"


def test_genuinely_edited_file_is_real(tmp_path):
    repo = _seed_repo(tmp_path)
    (repo / "a.txt").write_text("someone's half-written work\n")

    report = _triage(repo)
    assert "REAL uncommitted work" in report

    _triage(repo, "--fix")
    assert (repo / "a.txt").read_text() == "someone's half-written work\n"


def _seed_stale_mix(tmp_path: Path) -> Path:
    """The 2026-08-15 incident shape: the tree copy carries commit A's
    edit but not commit B's, so its blob matches NO ancestor (A landed
    interleaved with B) while being strictly behind HEAD."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "doc.txt").write_text("base line\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "base")
    # Commit B: another session's hunk, landed first. Commit A': this
    # session's edit REPLAYED on top of B (the pre-rebase base+A blob
    # exists only in the discarded local commit, never in ancestry).
    (repo / "doc.txt").write_text("base line\nsession B landed hunk\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "B: landed from elsewhere")
    (repo / "doc.txt").write_text(
        "base line\nsession B landed hunk\nsession A edit\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "A: local edit (replayed)")
    # Materialization gap: tree copy = pre-rebase base + A only.
    (repo / "doc.txt").write_text("base line\nsession A edit\n")
    return repo


def test_stale_mix_is_behind_not_real_with_blame_evidence(tmp_path):
    """Pre-fix, this exact shape reported REAL (0 stale, 1 real) — the
    2026-08-15 misread. It must now surface as BEHIND with the landing
    commit named in the blame summary."""
    repo = _seed_stale_mix(tmp_path)

    report = _triage(repo)
    assert "BEHIND" in report
    assert "REAL" not in report
    assert "missing lines from" in report          # blame evidence
    assert "B: landed from elsewhere" in report    # names the landed commit
    assert "0 stale, 1 behind, 0 real." in report


def test_fix_leaves_behind_paths_alone(tmp_path):
    """Content can't distinguish stale-mix from a deletion-in-progress,
    so plain --fix must never restore BEHIND paths."""
    repo = _seed_stale_mix(tmp_path)

    out = _triage(repo, "--fix")
    assert "NOT restored" in out
    assert (repo / "doc.txt").read_text() == "base line\nsession A edit\n"


def test_fix_behind_restores_after_judgment(tmp_path):
    repo = _seed_stale_mix(tmp_path)

    _triage(repo, "--fix-behind")
    assert (repo / "doc.txt").read_text() == (
        "base line\nsession B landed hunk\nsession A edit\n")


def test_behind_blame_keeps_first_encountered_commits_not_hash_order(
        tmp_path):
    """r1+r2: sort -u ordered blamed SHAs lexicographically, so head -3
    sampled an arbitrary 3-of-N — and a 2-commit fixture can't tell the
    difference (r2: both survive either way). This one can: FOUR landing
    commits blame the missing range, so the head -3 cap must keep the
    first three in file order (L1 L2 L3) and truncate L4. Commit dates
    are pinned so SHAs — and therefore the pre-fix hash order — are
    deterministic: under sort -u the surviving trio is the three
    lexicographically smallest SHAs, and the test's precondition assert
    guarantees that trio differs from {L1,L2,L3}."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    lines = ["one\n"]
    (repo / "doc.txt").write_text("".join(lines))
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "base", date="2026-01-01T00:00:00 +0000")
    landing = {}
    for i in (1, 2, 3, 4):
        lines.append(f"from-L{i}\n")
        (repo / "doc.txt").write_text("".join(lines))
        _git(repo, "add", "doc.txt")
        # L1's trailing " a" is load-bearing: with pinned dates it sets
        # the deterministic SHA order to L1<L4<L2<L3, so sort -u|head -3
        # would keep L4 and drop L3 — the precondition below verifies.
        _git(repo, "commit", "-qm",
             "L1 lands a" if i == 1 else f"L{i} lands",
             date=f"2026-01-0{i + 1}T00:00:00 +0000")
        landing[f"L{i}"] = _git(repo, "rev-parse", "HEAD").strip()
    lines.append("keeper\n")
    (repo / "doc.txt").write_text("".join(lines))
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "E lands keeper",
         date="2026-01-06T00:00:00 +0000")
    # Stale-mix tree copy: base + keeper only. Matches no ancestor blob
    # (only E has "keeper", and E also has L1-L4's lines); missing lines
    # are one contiguous 4-line hunk blaming to four distinct commits.
    (repo / "doc.txt").write_text("one\nkeeper\n")

    # Precondition that makes this a genuine pre-fix failer: the three
    # lexicographically smallest SHAs (what sort -u|head -3 would keep)
    # must differ from the first-three-in-file-order set. Deterministic
    # because commit dates are pinned. If a fixture edit ever breaks
    # this, fail loudly rather than silently pinning nothing.
    hash_trio = set(sorted(landing.values())[:3])
    file_trio = {landing["L1"], landing["L2"], landing["L3"]}
    assert hash_trio != file_trio, (
        "fixture no longer discriminates dedup order — adjust a commit "
        "message/date so the SHA order changes")

    report = _triage(repo)
    assert "BEHIND" in report
    assert "L1 lands" in report
    assert "L2 lands" in report
    assert "L3 lands" in report
    assert "L4 lands" not in report  # truncated by the head -3 cap


def test_deletion_only_edit_reads_behind_but_survives_fix(tmp_path):
    """The honest ambiguity, pinned: a REAL deletion-only edit (another
    session removing lines mid-chunk) is content-identical to stale-mix
    and now reads BEHIND — the contract that matters is that --fix
    leaves it untouched."""
    repo = _seed_repo(tmp_path)
    (repo / "a.txt").write_text("")  # deleted the only line, kept file

    report = _triage(repo)
    assert "BEHIND" in report

    _triage(repo, "--fix")
    assert (repo / "a.txt").read_text() == ""
