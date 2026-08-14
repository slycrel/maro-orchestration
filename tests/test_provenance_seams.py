"""Structural tripwires for the run paper trail (BACKLOG LT-0, EDGE 6 + EDGE 7).

These are census-style guards in the spirit of
`test_captains_log.py::test_event_contract_doc_covers_all_types`: they don't
exercise behavior, they assert that every call site of a provenance seam
actually feeds it. Both edges below were found by a workspace census AFTER the
fact, on live data — the point of these tests is that the next such gap fails
the suite instead of quietly producing months of unattributable runs.

If one of these fails, the fix is to add the argument at the new call site.
Adding to an allowlist is a deliberate act that needs a reason in the entry.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _strip_docstrings(text: str) -> str:
    """Blank out triple-quoted blocks, preserving newlines so line numbers hold.

    Prose mentions a seam constantly ("Passed through to adapter.complete()",
    module docstrings naming write_run_report). Scanning raw source flags those
    as unlabeled call sites. Blanking rather than deleting keeps every reported
    line number pointing at the real file.
    """
    def _blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    return re.sub(r'("""|\'\'\')(?:.|\n)*?\1', _blank, text)


def _call_text(text: str, open_paren_idx: int, limit: int = 8000) -> str:
    """Return the full parenthesized call starting at `open_paren_idx`."""
    depth = 0
    for j in range(open_paren_idx, min(len(text), open_paren_idx + limit)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren_idx:j + 1]
    return ""


# ---------------------------------------------------------------------------
# EDGE 7 — the live report dropped operator injections
# ---------------------------------------------------------------------------
# Only the three loop_finalize call sites passed `injections=`, so an operator
# who injected a note mid-run and opened the report to confirm it landed saw an
# empty panel until the run finalized.

def test_every_write_run_report_call_passes_injections():
    offenders = []
    for path in sorted(SRC.glob("*.py")):
        text = _strip_docstrings(path.read_text(encoding="utf-8", errors="replace"))
        for m in re.finditer(r"_write_run_report\(|write_run_report\(", text):
            # Skip the definition itself and any import line.
            line_start = text.rfind("\n", 0, m.start()) + 1
            line = text[line_start:text.find("\n", m.start())]
            if line.lstrip().startswith(("def ", "from ", "import ")):
                continue
            call = _call_text(text, m.end() - 1)
            if not call:
                continue
            if "injections=" not in call:
                lineno = text[:m.start()].count("\n") + 1
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "run-report call sites missing injections= (the live report will show "
        "an empty operator-injections panel until finalize): " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# EDGE 6 — per-layer cost attribution broke at the work layer
# ---------------------------------------------------------------------------
# `purpose=` on a call record is the per-layer key. 73 of 82 real call sites
# carried it, but 4 of the 9 unlabeled ones were the agentic executor seams —
# the biggest token consumers in any run — so "what did the harness spend vs
# what did the WORK spend" fell back to a prompt-opener heuristic.

# Deliberate exemptions. Each needs a reason, not just a path.
_PURPOSE_EXEMPT = {
    # FailoverAdapter pops `purpose` from kwargs (llm.py) and stamps it onto
    # the record itself; forwarding it to the inner adapter would be wrong.
    "llm.py",
    # Forwarding wrapper: `call_kwargs = dict(kwargs)` then splat, so whatever
    # purpose the ORIGIN caller supplied flows through untouched. Labelling
    # here would overwrite the real layer with "hosted_free".
    "hosted_free.py",
}


def _is_code_position(text: str, idx: int) -> bool:
    """False when `idx` sits inside a line comment or a single-line string.

    Prose names these seams constantly — a dataclass field comment
    (`# ... "ClaudeSubprocessAdapter.complete() timeout"`) and a suggestion
    string (`"Consider adding **kwargs to adapter.complete()"`) both scan as
    call sites otherwise.
    """
    line_start = text.rfind("\n", 0, idx) + 1
    prefix = text[line_start:idx]
    if "#" in prefix:
        return False
    # Odd quote count before the match ⇒ we are inside a string literal.
    return prefix.count('"') % 2 == 0 and prefix.count("'") % 2 == 0


def _kwargs_dict_has_purpose(text: str, lineno: int, call: str) -> bool:
    """True if the call splats a **kwargs dict that carries a purpose key.

    planner.py builds `_staged_kwargs = {..., "purpose": "decompose-staged"}`
    and splats it, so a naive scan of the call text alone under-reports.
    """
    lines = text.split("\n")
    for kw in re.findall(r"\*\*(\w+)", call):
        block = "\n".join(lines[max(0, lineno - 80):lineno])
        # Scan EVERY occurrence, not just the last. planner.py builds the dict
        # (with "purpose") and then mutates it a few lines later
        # (`_staged_kwargs["thinking_budget"] = ...`); anchoring on the last
        # mention looks forward from the mutation and misses the literal.
        for m in re.finditer(re.escape(kw), block):
            if "purpose" in block[m.start():m.start() + 800]:
                return True
        # dict built then mutated: `_kw["purpose"] = ...`
        if re.search(rf'{re.escape(kw)}\[["\']purpose["\']\]', block):
            return True
    return False


def test_agentic_executor_calls_carry_a_purpose_label():
    """Every LLM call site must be attributable to a harness layer."""
    offenders = []
    for path in sorted(SRC.glob("*.py")):
        if path.name in _PURPOSE_EXEMPT:
            continue
        text = _strip_docstrings(path.read_text(encoding="utf-8", errors="replace"))
        for m in re.finditer(r"\b\w*[Aa]dapter\w*\.complete\(", text):
            call = _call_text(text, m.end() - 1)
            if not call:
                continue
            lineno = text[:m.start()].count("\n") + 1
            if not _is_code_position(text, m.start()):
                continue
            if "purpose=" in call or '"purpose"' in call:
                continue
            if _kwargs_dict_has_purpose(text, lineno, call):
                continue
            offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "LLM call sites with no purpose= label — their call records cannot be "
        "attributed to a harness layer, so per-layer cost analysis falls back "
        "to prompt sniffing: " + ", ".join(offenders)
    )


def test_the_four_known_agentic_seams_are_labelled():
    """Narrow, explicit pin on the seams EDGE 6 actually found.

    The broad test above can be satisfied by an exemption; this one names the
    four biggest token consumers so silencing them takes a visible edit.
    """
    expected = {
        "step_exec.py": "step-execute",
        "workers.py": "worker-ticket",
        "team.py": "team-worker",
    }
    for filename, label in expected.items():
        text = (SRC / filename).read_text(encoding="utf-8", errors="replace")
        assert f'"{label}"' in text, (
            f"{filename} lost its {label!r} purpose label — the agentic "
            "executor seam is unattributable again"
        )


class TestContestedByClosure:
    """The disagreement record (2026-08-02). Two live false demotions —
    9d88acf2 (glob claim read as a literal filename, closure 0.75) and
    ea4ebe4a (a forensic run flagged for "claiming" the very file it was
    reporting had never been written, closure 0.92 on 5/5 checks) — were
    each found by hand days later. The guard still wins; it just stops
    winning invisibly."""

    def _closure(self, complete=True, confidence=0.92):
        return type("C", (), {"complete": complete, "confidence": confidence})()

    def test_records_the_conflict_when_closure_says_complete(self):
        from provenance import contested_by_closure
        got = contested_by_closure(
            self._closure(), ["artifacts/comm-examples.md (claimed written, not found)"])
        assert got["goal_verdict_contested"] is True
        assert got["goal_verdict_contested_by"] == "closure"
        assert got["closure_confidence"] == 0.92
        assert "comm-examples" in got["provenance_missing_claims"][0]

    def test_silent_when_closure_agrees_the_run_failed(self):
        """No conflict, no noise — the guard and closure concurring is the
        normal case and must stay unannotated."""
        from provenance import contested_by_closure
        assert contested_by_closure(self._closure(complete=False), ["x"]) == {}

    def test_silent_when_there_was_no_closure_verdict(self):
        from provenance import contested_by_closure
        assert contested_by_closure(None, ["x"]) == {}

    def test_never_raises_on_a_malformed_confidence(self):
        """Fails open: an annotation must never be able to break a run."""
        from provenance import contested_by_closure
        bad = type("C", (), {"complete": True, "confidence": "not-a-number"})()
        assert contested_by_closure(bad, ["x"])["closure_confidence"] == 0.0

    def test_missing_claims_are_capped(self):
        from provenance import contested_by_closure
        got = contested_by_closure(self._closure(), [f"f{i}.md" for i in range(50)])
        assert len(got["provenance_missing_claims"]) == 10

    def test_demotion_still_stands(self):
        """Load-bearing: this is an annotation, not an override. The guard
        exists to catch the false_pass a text-only verdict can't see."""
        from provenance import contested_by_closure
        got = contested_by_closure(self._closure(), ["x"])
        assert "goal_achieved" not in got
        assert "goal_verdict_source" not in got


class TestNowVerdictRationale:
    """ed7cf400 presented as 'incomplete, no explanation' for a month while
    its judge's reasoning sat in build/calls/. Found by ea4ebe4a (LT-1 #8)."""

    def _rationale(self):
        import handle
        return handle._now_verdict_rationale

    def test_strips_a_fenced_json_verdict_and_keeps_the_reason(self):
        raw = ('```json\n{"fulfilled": false}\n```\n\nThe response claims the '
               'task is complete but provides no evidence:\n- No Write or Bash '
               'tool calls showing the file was created')
        got = self._rationale()(raw)
        assert got.startswith("The response claims")
        assert "No Write or Bash tool calls" in got
        assert "fulfilled" not in got

    def test_strips_a_bare_json_verdict(self):
        got = self._rationale()('{"fulfilled": false} Nothing was written.')
        assert got == "Nothing was written."

    def test_verdict_with_no_prose_yields_empty_not_json(self):
        """Better to record nothing than to record the boolean twice."""
        assert self._rationale()('```json\n{"fulfilled": false}\n```') == ""

    def test_plain_prose_survives_untouched(self):
        assert self._rationale()("Just a reason.") == "Just a reason."

    def test_output_is_bounded(self):
        # VERDICT_PROSE_CAP + clip marker since the 2026-08-14 fixpoint
        # round (the old bare 400 cut the judge's reason silently — the
        # exact loss this seam exists to prevent).
        from context_budget import VERDICT_PROSE_CAP
        out = self._rationale()("x " * 5000)
        assert len(out) <= VERDICT_PROSE_CAP + 64
        assert "truncated" in out

    def test_empty_and_none_are_safe(self):
        assert self._rationale()("") == ""
        assert self._rationale()(None) == ""


class TestNowJudgeReason:
    """Found live, not by pins (run 2113a608, 2026-08-02): the propagation
    fix landed INERT on the hosted-free judge because that judge ran with
    max_tokens=64 — room for `{"fulfilled": false}` and nothing else. There
    was no rationale to propagate. The reason has to be ASKED for."""

    def test_prompt_requests_a_reason_on_false_only(self):
        import handle
        sys_prompt = handle._NOW_VERIFY_SYSTEM
        assert '"why"' in sys_prompt
        assert "Omit `why` when fulfilled" in sys_prompt

    def test_judge_has_room_to_answer(self):
        """A cap that fits only the verdict silently forecloses the reason."""
        import inspect
        import handle
        src = inspect.getsource(handle._verify_now_outcome)
        assert "max_tokens=160" in src
        assert "max_tokens=64" not in src

    def test_structured_why_is_preferred_over_scraped_prose(self):
        """Both are supported; the structured field wins when present."""
        import inspect
        import handle
        src = inspect.getsource(handle._verify_now_outcome)
        assert 'verdict.get("why")' in src
        assert "_now_verdict_rationale(resp.content)" in src
