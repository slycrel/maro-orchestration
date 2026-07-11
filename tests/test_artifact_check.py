"""Tests for the filesystem ground-truth fabrication check (artifact_check.py).

Covers the done≠achieved gap: a step claims a write but produces no artifact.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from artifact_check import (  # noqa: E402
    ArtifactVerdict,
    _claims_concrete_stdout,
    _claims_clean_success,
    _python_is_inert,
    changed_since,
    check_execution_claim,
    check_fabrication,
    extract_write_claims,
    snapshot_dir,
)


def _bash(command="pytest -q", output="", is_error=False):
    return {"name": "Bash", "input": {"command": command}, "output": output, "is_error": is_error}


# Module bodies used by the inert-output tests.
_INERT_FIZZBUZZ = '''
def fizzbuzz(n):
    """Return the FizzBuzz string for n."""
    if n % 15 == 0:
        return "FizzBuzz"
    return str(n)
'''

_LIVE_FIZZBUZZ = _INERT_FIZZBUZZ + '''
if __name__ == "__main__":
    for i in range(1, 16):
        print(fizzbuzz(i))
'''


# --- extract_write_claims -------------------------------------------------

def test_extracts_basic_write_claims():
    assert extract_write_claims("Wrote the output to fizzbuzz.py") == ["fizzbuzz.py"]
    assert extract_write_claims("Saved results to data/out.json") == ["data/out.json"]
    assert extract_write_claims("Created the file as report.md") == ["report.md"]


def test_dedupes_claims():
    txt = "Wrote to a.py. Later saved to a.py again."
    assert extract_write_claims(txt) == ["a.py"]


def test_ignores_non_file_targets():
    # No extension => not a file claim.
    assert extract_write_claims("Saved to memory") == []
    assert extract_write_claims("Wrote the value into the database") == []
    assert extract_write_claims("Stored as a draft") == []


def test_ignores_bare_mentions_without_verb():
    # A path mention with no write verb is not a claim.
    assert extract_write_claims("The script fizzbuzz.py prints numbers") == []


def test_empty_and_none_text():
    assert extract_write_claims("") == []
    assert extract_write_claims(None) == []


# --- snapshot_dir / changed_since ----------------------------------------

def test_snapshot_empty_for_missing_root():
    assert snapshot_dir(None) == {}
    assert snapshot_dir("/nonexistent/path/xyz") == {}


def test_snapshot_and_changed_detects_new_file(tmp_path):
    before = snapshot_dir(tmp_path)
    assert before == {}
    (tmp_path / "new.py").write_text("print(1)")
    changed = changed_since(before, tmp_path)
    assert "new.py" in changed


def test_changed_skips_unmodified(tmp_path):
    (tmp_path / "stable.py").write_text("x")
    before = snapshot_dir(tmp_path)
    # No change => empty diff.
    assert changed_since(before, tmp_path) == set()


def test_snapshot_skips_vcs_dirs(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref")
    (tmp_path / "real.py").write_text("x")
    snap = snapshot_dir(tmp_path)
    assert "real.py" in snap
    assert not any(k.startswith(".git") for k in snap)


# --- check_fabrication ----------------------------------------------------

def test_fabricated_when_claim_but_no_artifact(tmp_path):
    before = snapshot_dir(tmp_path)
    v = check_fabrication("Wrote the solution to fizzbuzz.py", str(tmp_path), before)
    assert v.fabricated is True
    assert v.claims == ["fizzbuzz.py"]
    assert v.missing == ["fizzbuzz.py"]
    assert v.changed_count == 0


def test_not_fabricated_when_file_in_diff(tmp_path):
    before = snapshot_dir(tmp_path)
    (tmp_path / "fizzbuzz.py").write_text("print('fizz')")
    v = check_fabrication("Wrote the solution to fizzbuzz.py", str(tmp_path), before)
    assert v.fabricated is False
    assert v.changed_count >= 1


def test_not_fabricated_when_claimed_file_exists_in_project_dir(tmp_path):
    # File already existed before the step (existence escape — real work elsewhere).
    (tmp_path / "fizzbuzz.py").write_text("old")
    before = snapshot_dir(tmp_path)
    v = check_fabrication("Wrote the solution to fizzbuzz.py", str(tmp_path), before)
    assert v.fabricated is False


def test_not_fabricated_for_absolute_path_that_exists(tmp_path):
    target = tmp_path / "out.json"
    target.write_text("{}")
    before = {}  # diff irrelevant; absolute path exists
    other = tmp_path / "elsewhere"
    other.mkdir()
    v = check_fabrication(f"Saved results to {target}", str(other), before)
    assert v.fabricated is False


def test_not_fabricated_when_no_claim(tmp_path):
    before = snapshot_dir(tmp_path)
    v = check_fabrication("Analyzed the data and found three patterns.", str(tmp_path), before)
    assert v.fabricated is False
    assert v.claims == []


def test_multiple_claims_one_real_not_fabricated(tmp_path):
    before = snapshot_dir(tmp_path)
    (tmp_path / "a.py").write_text("x")
    v = check_fabrication("Wrote to a.py and saved to b.py", str(tmp_path), before)
    # b.py absent, but a.py landed => real work happened, not fabrication.
    assert v.fabricated is False


def test_fail_open_on_bad_snapshot(tmp_path):
    # A malformed before-snapshot must not raise; verdict should be safe.
    # The file must exist so changed_since actually compares mtime (float) against
    # the bad value (str), triggering the internally-caught TypeError => fail-open.
    (tmp_path / "x.py").write_text("data")
    v = check_fabrication("Wrote to x.py", str(tmp_path), {"x.py": "not-a-float"})
    assert isinstance(v, ArtifactVerdict)
    assert v.fabricated is False


def test_missing_artifact_sets_kind(tmp_path):
    before = snapshot_dir(tmp_path)
    v = check_fabrication("Saved results to out.json", str(tmp_path), before)
    assert v.fabricated is True
    assert v.kind == "missing-artifact"


# --- Layer 2: inert-output (claimed stdout from a definitions-only file) ---

def test_python_is_inert_detects_definitions_only():
    assert _python_is_inert(_INERT_FIZZBUZZ) is True


def test_python_is_inert_false_with_main_block():
    assert _python_is_inert(_LIVE_FIZZBUZZ) is False


def test_python_is_inert_false_with_toplevel_print():
    assert _python_is_inert("print('hi')") is False


def test_python_is_inert_docstring_only():
    assert _python_is_inert('"""just a docstring"""') is True


def test_python_is_inert_none_on_syntax_error():
    assert _python_is_inert("def (:::") is None


def test_claims_concrete_stdout_positive():
    assert _claims_concrete_stdout("Verified output: 1,2,Fizz,4,Buzz,FizzBuzz") is True
    assert _claims_concrete_stdout("Running it prints '42' to stdout") is True


def test_claims_concrete_stdout_excludes_function_returns():
    # A "returns" claim is true of an inert module — must not count as stdout.
    assert _claims_concrete_stdout("The function returns FizzBuzz for 15") is False


def test_claims_concrete_stdout_requires_concrete_content():
    # stdout verb but no concrete digits/quotes => not actionable.
    assert _claims_concrete_stdout("It prints the result") is False


def test_inert_output_flagged(tmp_path):
    # The organic repro: file exists (so missing-artifact passes) but is inert,
    # and the step narrates concrete output it cannot have produced.
    (tmp_path / "fizzbuzz.py").write_text(_INERT_FIZZBUZZ)
    before = {}  # file already present; the diff is irrelevant to this layer
    v = check_fabrication(
        "Wrote fizzbuzz.py and verified output: 1,2,Fizz,4,Buzz,FizzBuzz",
        str(tmp_path), before,
    )
    assert v.fabricated is True
    assert v.kind == "inert-output"


def test_live_file_with_output_not_flagged(tmp_path):
    (tmp_path / "fizzbuzz.py").write_text(_LIVE_FIZZBUZZ)
    before = {}
    v = check_fabrication(
        "Wrote fizzbuzz.py and verified output: 1,2,Fizz,4,Buzz,FizzBuzz",
        str(tmp_path), before,
    )
    assert v.fabricated is False


def test_inert_file_without_output_claim_not_flagged(tmp_path):
    # An inert helper module with no stdout claim is perfectly legitimate.
    (tmp_path / "helpers.py").write_text(_INERT_FIZZBUZZ)
    before = snapshot_dir(tmp_path)
    v = check_fabrication("Added helper functions to helpers.py", str(tmp_path), before)
    assert v.fabricated is False


def test_write_ish_words_with_empty_diff_not_flagged(tmp_path):
    # Absence-based flagging was rejected: write-ish words + empty diff is NOT
    # evidence of fabrication (analysis/out-of-workspace work leaves it empty).
    before = snapshot_dir(tmp_path)
    v = check_fabrication("Created the solution and implemented the fix.", str(tmp_path), before)
    assert v.fabricated is False


def test_pure_analysis_not_flagged(tmp_path):
    before = snapshot_dir(tmp_path)
    v = check_fabrication("Analyzed the data and summarized three findings.", str(tmp_path), before)
    assert v.fabricated is False


# --- check_execution_claim (exec-contradiction) ---------------------------

class TestExecutionClaim:
    def test_fabricated_when_all_runs_failed_but_claims_success(self):
        v = check_execution_claim(
            "Ran the test suite — all 142 tests passed.",
            [_bash("pytest -q", output="ImportError", is_error=True)],
        )
        assert v.fabricated is True
        assert v.kind == "execution-contradiction"
        assert "pytest" in v.reason

    def test_not_flagged_when_result_acknowledges_failure(self):
        # Agent is honest about the failure → not a fabrication.
        v = check_execution_claim(
            "Tried to run the tests but pytest failed with an ImportError.",
            [_bash("pytest -q", is_error=True)],
        )
        assert v.fabricated is False

    def test_not_flagged_fix_then_succeed(self):
        # First run failed, a later run succeeded → legitimate; success claim ok.
        v = check_execution_claim(
            "Fixed the import and the tests pass now.",
            [_bash("pytest -q", is_error=True), _bash("pytest -q", output="142 passed", is_error=False)],
        )
        assert v.fabricated is False

    def test_not_flagged_when_run_succeeded(self):
        v = check_execution_claim(
            "All tests passed.",
            [_bash("pytest -q", output="142 passed", is_error=False)],
        )
        assert v.fabricated is False

    def test_not_flagged_when_no_execution_tools(self):
        # Only file tools ran — the per-step transcript shows no command; we do
        # NOT flag (could reference a prior step's run).
        v = check_execution_claim(
            "The tests pass.",
            [{"name": "Write", "input": {"file_path": "x.py"}, "is_error": False}],
        )
        assert v.fabricated is False

    def test_not_flagged_empty_or_none_transcript(self):
        assert check_execution_claim("All tests passed.", None).fabricated is False
        assert check_execution_claim("All tests passed.", []).fabricated is False

    def test_no_success_claim_no_flag_even_if_runs_failed(self):
        # Result makes no success claim at all → nothing to contradict.
        v = check_execution_claim(
            "Investigated the build configuration.",
            [_bash("make", is_error=True)],
        )
        assert v.fabricated is False

    def test_fail_open_on_garbage(self):
        assert check_execution_claim("passed", ["not-a-dict", 42]).fabricated is False

    def test_claims_clean_success_helper(self):
        assert _claims_clean_success("all tests passed") is True
        assert _claims_clean_success("exit code 0, works") is True
        # Acknowledged failure suppresses the success signal.
        assert _claims_clean_success("tests passed but one failed") is False
        assert _claims_clean_success("did the analysis") is False


class TestScavengeDetector:
    """detect_out_of_fence_access — BACKLOG #1 contamination-visibility diagnostic."""

    def _fence(self, tmp_path):
        proj = tmp_path / "ws" / "projects" / "demo"
        proj.mkdir(parents=True)
        ws = tmp_path / "ws"
        return proj, ws

    def test_empty_events_no_flag(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        report = detect_out_of_fence_access([], [str(proj), str(ws)])
        assert not report.flagged

    def test_in_fence_read_not_flagged(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        events = [{"name": "Read", "input": {"file_path": str(proj / "notes.md")}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert not report.flagged

    def test_out_of_fence_read_flagged(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        stray = tmp_path / "elsewhere" / "stale-clone" / "main.go"
        events = [{"name": "Read", "input": {"file_path": str(stray)}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert report.flagged
        assert report.reads == [{"path": str(stray), "tool": "Read"}]
        assert report.writes == []

    def test_out_of_fence_write_flagged_separately(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        stray = tmp_path / "repo" / "leaked.md"
        events = [{"name": "Write", "input": {"file_path": str(stray)}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert report.writes == [{"path": str(stray), "tool": "Write"}]
        assert report.reads == []

    def test_relative_paths_ignored(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        events = [{"name": "Read", "input": {"file_path": "notes.md"}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert not report.flagged

    def test_system_paths_ignored(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        events = [
            {"name": "Read", "input": {"file_path": "/etc/hosts"}},
            {"name": "Bash", "input": {"command": "/usr/bin/python3 run.py"}},
        ]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert not report.flagged

    def test_bash_command_paths_scanned(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        stray = tmp_path / "other-project" / "config.yml"
        events = [{"name": "Bash", "input": {"command": f"cat {stray}"}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert report.flagged
        assert report.reads[0]["tool"] == "Bash"
        assert report.reads[0]["path"] == str(stray)

    def test_bash_in_fence_paths_not_flagged(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        events = [{"name": "Bash", "input": {"command": f"ls {proj}/artifacts"}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert not report.flagged

    def test_dedup_and_cap(self, tmp_path):
        from artifact_check import detect_out_of_fence_access, _SCAVENGE_CAP
        proj, ws = self._fence(tmp_path)
        dup = str(tmp_path / "x" / "same.txt")
        events = [{"name": "Read", "input": {"file_path": dup}} for _ in range(5)]
        events += [
            {"name": "Read", "input": {"file_path": str(tmp_path / "x" / f"f{i}.txt")}}
            for i in range(_SCAVENGE_CAP + 10)
        ]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert len(report.reads) == _SCAVENGE_CAP
        assert report.truncated
        assert sum(1 for r in report.reads if r["path"] == dup) == 1

    def test_never_raises_on_garbage(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        events = [None, {"name": "Read", "input": "not-a-dict"}, {"input": {}}, 42]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert not report.flagged

    def test_empty_fence_roots_skipped(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        stray = str(tmp_path / "anywhere.txt")
        events = [{"name": "Read", "input": {"file_path": stray}}]
        report = detect_out_of_fence_access(events, ["", str(tmp_path / "ws")])
        assert report.flagged


class TestScavengeUrlFalsePositives:
    """First organic scavenge rows (2026-07-03, run 15f2e3d4) flagged
    '/owasp.org/www-community/...' and '/docs.python.org/...' — URL paths in
    Bash commands matched as absolute filesystem paths (the regex could start
    a match at the second slash of 'https://'). URLs and colon-prefixed
    remote/PATH-style entries must not pollute the fence evidence stream."""

    def _fence(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        return [str(proj)]

    def test_https_url_in_bash_not_flagged(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        events = [{"name": "Bash", "input": {
            "command": "curl -s https://owasp.org/www-community/attacks/Path_Traversal"}}]
        report = detect_out_of_fence_access(events, self._fence(tmp_path))
        assert not report.flagged, report

    def test_http_url_not_flagged(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        events = [{"name": "Bash", "input": {
            "command": "wget http://docs.python.org/3/library/pathlib.html -O out.html"}}]
        report = detect_out_of_fence_access(events, self._fence(tmp_path))
        assert not report.flagged, report

    def test_colon_prefixed_remote_path_not_flagged(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        events = [{"name": "Bash", "input": {
            "command": "scp report.txt host.example.com:/home/remote/inbox/"}}]
        report = detect_out_of_fence_access(events, self._fence(tmp_path))
        assert not report.flagged, report

    def test_plain_absolute_path_still_flagged(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        events = [{"name": "Bash", "input": {
            "command": "cat /home/nonexistent-stale-clone/main.go"}}]
        report = detect_out_of_fence_access(events, self._fence(tmp_path))
        assert report.flagged
        assert report.reads[0]["path"] == "/home/nonexistent-stale-clone/main.go"

    # -- run 3bffa6d6 specimens (2026-07-11): markup fragments in Bash text --

    def test_xml_closing_tags_in_parse_code_not_flagged(self, tmp_path):
        # Worker wrote a Python regex over fetched KML; closing tags matched
        # as absolute paths ("/phoneNumber", "/coordinates").
        from artifact_check import detect_out_of_fence_access
        events = [{"name": "Bash", "input": {"command": (
            "python3 - <<'EOF'\nimport re\n"
            "placemarks = re.findall(r'<name>(.*?)</name><description>(.*?)"
            "</description><phoneNumber>(.*?)</phoneNumber><Point>"
            "<coordinates>(.*?)</coordinates>', xml)\nEOF"
        )}}]
        report = detect_out_of_fence_access(events, self._fence(tmp_path))
        assert not report.flagged, report

    def test_url_path_in_prose_and_patterns_not_flagged(self, tmp_path):
        # "/api" in an echo'd label and "/static/js/..." in a grep pattern —
        # path-shaped web fragments whose first segment is no local directory.
        from artifact_check import detect_out_of_fence_access
        events = [{"name": "Bash", "input": {"command": (
            'echo "--- grep for /api or .json patterns ---"\n'
            'grep -o \'src="/static/js/main.606fbec2.js"\' page.html'
        )}}]
        report = detect_out_of_fence_access(events, self._fence(tmp_path))
        assert not report.flagged, report

    def test_real_root_nonexistent_leaf_still_flagged(self, tmp_path):
        # The first-segment-is-a-real-dir rule must NOT eat the stale-clone
        # diagnostic: /home exists even when the clone under it doesn't.
        from artifact_check import detect_out_of_fence_access
        events = [{"name": "Bash", "input": {"command": (
            'grep -r pattern /home/gone-clone/src/'
        )}}]
        report = detect_out_of_fence_access(events, self._fence(tmp_path))
        assert report.flagged
        assert report.reads[0]["path"] == "/home/gone-clone/src"


class TestScavengeCwdDrift:
    """cwd-drift write detection — the run-668e46d1 evasion specimen (2026-07-04):
    a worker cd's out of the fence mid-command and writes with relative paths,
    invisible to both the absolute-path scan and the structured-tool check."""

    def _fence(self, tmp_path):
        proj = tmp_path / "ws" / "projects" / "demo"
        proj.mkdir(parents=True)
        ws = tmp_path / "ws"
        return proj, ws

    def test_specimen_cd_then_relative_redirect_same_command(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        repo = tmp_path / "repo"
        events = [{"name": "Bash", "input": {
            "command": f"cd {repo} && python3 -c 'print(1)' > scripts/count-lines.py"}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert {"path": str(repo / "scripts" / "count-lines.py"),
                "tool": "Bash(cwd-drift)"} in report.writes

    def test_drift_persists_across_bash_calls(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        repo = tmp_path / "repo"
        events = [
            {"name": "Bash", "input": {"command": f"cd {repo}"}},
            {"name": "Bash", "input": {"command": "echo hi > stray.txt"}},
        ]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert {"path": str(repo / "stray.txt"),
                "tool": "Bash(cwd-drift)"} in report.writes

    def test_relative_structured_write_after_drift(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        repo = tmp_path / "repo"
        events = [
            {"name": "Bash", "input": {"command": f"cd {repo}"}},
            {"name": "Write", "input": {"file_path": "scripts/x.py"}},
        ]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert {"path": str(repo / "scripts" / "x.py"),
                "tool": "Write(cwd-drift)"} in report.writes

    def test_cd_back_into_fence_stops_flagging(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        repo = tmp_path / "repo"
        events = [
            {"name": "Bash", "input": {"command": f"cd {repo}"}},
            {"name": "Bash", "input": {"command": f"cd {proj} && echo hi > fine.txt"}},
        ]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert report.writes == []

    def test_relative_write_without_drift_not_flagged(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        events = [{"name": "Bash", "input": {"command": "echo hi > notes.txt"}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert report.writes == []

    def test_unresolvable_cd_goes_silent_not_guessing(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        events = [{"name": "Bash", "input": {"command": "cd $BUILD_DIR && echo x > out.txt"}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert report.writes == []

    def test_bare_cd_resolves_to_home(self, tmp_path, monkeypatch):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        fake_home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(fake_home))
        events = [{"name": "Bash", "input": {"command": "cd && echo hi > stray.txt"}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert {"path": str(fake_home / "stray.txt"),
                "tool": "Bash(cwd-drift)"} in report.writes

    def test_relative_cd_resolves_against_fence_base(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        # cd ../../.. from the project dir walks out of the fence
        events = [{"name": "Bash", "input": {
            "command": "cd ../../.. && echo hi > stray.txt"}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert {"path": str(tmp_path / "stray.txt"),
                "tool": "Bash(cwd-drift)"} in report.writes

    def test_append_redirect_and_tee_flagged(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        repo = tmp_path / "repo"
        events = [{"name": "Bash", "input": {
            "command": f"cd {repo} && echo a >> log.txt; echo b | tee out.txt"}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        paths = {w["path"] for w in report.writes}
        assert str(repo / "log.txt") in paths
        assert str(repo / "out.txt") in paths

    def test_cdrecord_is_not_cd(self, tmp_path):
        from artifact_check import detect_out_of_fence_access
        proj, ws = self._fence(tmp_path)
        events = [{"name": "Bash", "input": {"command": "cdrecord dev=1 && echo x > f.txt"}}]
        report = detect_out_of_fence_access(events, [str(proj), str(ws)])
        assert report.writes == []


class TestGoalDeclaredRoots:
    """Fence intent-widening (2026-07-04): paths the goal text names become
    extra fence roots — intent trumps."""

    def test_absolute_path_extracted(self):
        from artifact_check import goal_declared_roots
        roots = goal_declared_roots("Fix the flaky test in /home/clawd/claude/some-repo and rerun")
        assert roots == ["/home/clawd/claude/some-repo"]

    def test_tilde_path_expanded(self):
        import os
        from artifact_check import goal_declared_roots
        roots = goal_declared_roots("update ~/claude/other-repo/README.md please")
        assert roots == [os.path.expanduser("~/claude/other-repo/README.md")]

    def test_trailing_punctuation_stripped(self):
        from artifact_check import goal_declared_roots
        assert goal_declared_roots("write it to /home/clawd/notes/report.md.") == \
            ["/home/clawd/notes/report.md"]

    def test_system_paths_never_widen(self):
        from artifact_check import goal_declared_roots
        assert goal_declared_roots("append my key to /etc/passwd and /usr/local/bin/x") == []

    def test_bare_top_level_too_broad(self):
        from artifact_check import goal_declared_roots
        assert goal_declared_roots("clean up /data when done") == []

    def test_urls_and_word_slashes_ignored(self):
        from artifact_check import goal_declared_roots
        assert goal_declared_roots(
            "read https://owasp.org/www-project-top-ten/ and weigh read/write and/or tradeoffs"
        ) == []

    def test_dedup_and_cap(self):
        from artifact_check import goal_declared_roots, _GOAL_ROOTS_CAP
        goal = "sync /home/a/b with /home/a/b then " + " ".join(
            f"/home/x/d{i}" for i in range(12))
        roots = goal_declared_roots(goal)
        assert roots[0] == "/home/a/b"
        assert len(roots) == len(set(roots)) <= _GOAL_ROOTS_CAP

    def test_empty_and_none_safe(self):
        from artifact_check import goal_declared_roots
        assert goal_declared_roots("") == []
        assert goal_declared_roots(None) == []

    def test_declared_root_admits_writes_in_detector(self, tmp_path):
        """End-to-end at the detector: a write under a goal-declared root is
        not flagged when that root rides in fence_roots."""
        from artifact_check import detect_out_of_fence_access, goal_declared_roots
        proj = tmp_path / "proj"
        target = "/home/nonexistent-target-repo"
        events = [{"name": "Write", "input": {"file_path": f"{target}/fix.py"}}]
        base_roots = [str(proj), str(tmp_path / "ws")]
        assert detect_out_of_fence_access(events, base_roots).writes  # sanity: flagged without
        widened = base_roots + goal_declared_roots(f"fix the bug in {target}/fix.py")
        assert detect_out_of_fence_access(events, widened).writes == []


class TestFenceAllowRoots:
    """/tmp carve-out (2026-07-04): scratch is not drift."""

    def test_tmp_always_allowed(self):
        from artifact_check import fence_allow_roots
        assert "/tmp" in fence_allow_roots()

    def test_config_allowlist_included(self, monkeypatch):
        import config as config_mod
        from artifact_check import fence_allow_roots
        _orig = config_mod.get

        def _fake(key, default=None):
            if key == "validate.write_fence_allow":
                return ["~/scratch-area", "/mnt/shared"]
            return _orig(key, default)

        monkeypatch.setattr(config_mod, "get", _fake)
        import os
        roots = fence_allow_roots()
        assert os.path.expanduser("~/scratch-area") in roots
        assert "/mnt/shared" in roots

    def test_tmp_write_not_flagged_with_allow_roots(self, tmp_path):
        from artifact_check import detect_out_of_fence_access, fence_allow_roots
        events = [{"name": "Write", "input": {"file_path": "/tmp/maro-scratch/w.json"}}]
        base = [str(tmp_path / "proj"), str(tmp_path / "ws")]
        # Base fence alone would flag it — tmp_path lives under /tmp but the
        # scratch dir here is a sibling, not a child of the fence roots.
        report = detect_out_of_fence_access(events, base + fence_allow_roots())
        assert report.writes == []
