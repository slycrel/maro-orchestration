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
