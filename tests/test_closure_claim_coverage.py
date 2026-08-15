"""Claimed-but-unwired closure probe (review-miss taxonomy pattern 5).

Chunk 3 of the sanctioned 2026-08-16 sequence: for each behavioral
guarantee the deliverable's prose STATES, closure now looks for the
executing evidence. The plan call extracts stated guarantees and maps
each to the check that exercises it (or null); the join against executed
outcomes is deterministic; guarantees with no executing check surface to
the verdict judge as unverified claims and are recorded for the
box-side evidence gate (keep only if `unwired` fires on real runs).

Advisory v1 — mirrors the receipts posture: grounds the judge, never
flips a verdict deterministically.
"""
import json
from unittest.mock import MagicMock, patch

import closure_verify
from closure_verify import verify_goal_completion, _claim_coverage


def _adapter(plan_payload, verdict_payload):
    """Adapter returning a closure plan then a closure verdict (real JSON —
    extract_json runs unstubbed)."""
    adapter = MagicMock()
    responses = []
    for payload in (plan_payload, verdict_payload):
        resp = MagicMock()
        resp.content = json.dumps(payload)
        resp.input_tokens = 1
        resp.output_tokens = 1
        responses.append(resp)
    adapter.complete.side_effect = responses
    return adapter


_VERDICT_OK = {"complete": True, "confidence": 0.9, "gaps": [],
               "summary": "Goal achieved."}


class TestClaimCoverageJoin:
    """The deterministic join: guarantees → executed check outcomes,
    keyed by the plan's own check index (plan_index), immune to
    result-list reordering."""

    def test_statuses_join_by_plan_index(self, tmp_path):
        plan = {
            "checks": [
                {"description": "hard fail", "command": "false"},
                {"description": "passes", "command": "true"},
                {"description": "verifier tooling missing",
                 "command": "definitely_not_a_real_command_xyz"},
            ],
            "stated_guarantees": [
                {"guarantee": "warns when config missing", "check_index": 1},
                {"guarantee": "retries on failure", "check_index": 0},
                {"guarantee": "logs errors to file", "check_index": 2},
                {"guarantee": "caches results", "check_index": None},
                {"guarantee": "validates input", "check_index": 99},
                {"guarantee": "sends a notification", "check_index": True},
            ],
        }
        adapter = _adapter(plan, _VERDICT_OK)
        verdict = verify_goal_completion(
            "build a thing", [], adapter, workspace_path=str(tmp_path))
        cov = verdict.claim_coverage
        statuses = {e["guarantee"]: e["status"] for e in cov["guarantees"]}
        assert statuses == {
            "warns when config missing": "exercised",
            "retries on failure": "contradicted",
            "logs errors to file": "inconclusive",
            "caches results": "unwired",
            "validates input": "unwired",       # out-of-range index
            "sends a notification": "unwired",  # bool is not a valid index
        }
        assert cov["counts"] == {"exercised": 1, "contradicted": 1,
                                 "inconclusive": 1, "unwired": 3}

    def test_empty_command_row_does_not_shift_join(self, tmp_path):
        """A plan check with an empty command produces no result row; a
        positional join would silently attribute the next row's outcome
        to the wrong guarantee."""
        plan = {
            "checks": [
                {"description": "malformed", "command": ""},
                {"description": "passes", "command": "true"},
            ],
            "stated_guarantees": [
                {"guarantee": "mapped to the empty check", "check_index": 0},
                {"guarantee": "mapped to the real check", "check_index": 1},
            ],
        }
        adapter = _adapter(plan, _VERDICT_OK)
        verdict = verify_goal_completion(
            "build a thing", [], adapter, workspace_path=str(tmp_path))
        statuses = {e["guarantee"]: e["status"]
                    for e in verdict.claim_coverage["guarantees"]}
        assert statuses["mapped to the empty check"] == "unwired"
        assert statuses["mapped to the real check"] == "exercised"

    def test_preflight_prepend_does_not_shift_join(self, tmp_path):
        """A failing deliverable precondition PREPENDS synthetic rows to
        check_results — the off-by-injection class. The join must still
        land on the plan's own checks."""
        class _Deliv:
            name = "server"
            description = ""
            preconditions = ["./definitely_missing_run.sh"]
            shape = None

        class _Intent:
            deliverables = [_Deliv()]
            scope = None

        plan = {
            "checks": [{"description": "passes", "command": "true"}],
            "stated_guarantees": [
                {"guarantee": "responds on port 8080", "check_index": 0},
            ],
        }
        adapter = _adapter(plan, _VERDICT_OK)
        verdict = verify_goal_completion(
            "build a thing", [], adapter, workspace_path=str(tmp_path),
            resolved_intent=_Intent())
        # Premise check: the preflight row really was prepended.
        assert verdict.checks_run == 2
        statuses = {e["guarantee"]: e["status"]
                    for e in verdict.claim_coverage["guarantees"]}
        assert statuses == {"responds on port 8080": "exercised"}

    def test_cap_overflow_and_empty_text_dropped(self):
        """Bounded honestly: entries beyond the cap are counted in
        `overflow`, never silently dropped; empty guarantee text is not
        an entry."""
        raw = [{"guarantee": f"guarantee {i}", "check_index": None}
               for i in range(10)]
        raw.insert(0, {"guarantee": "   ", "check_index": None})
        cov = _claim_coverage(raw, [])
        assert len(cov["guarantees"]) == closure_verify._MAX_STATED_GUARANTEES
        assert cov["overflow"] == 10 - closure_verify._MAX_STATED_GUARANTEES
        assert cov["counts"]["unwired"] == closure_verify._MAX_STATED_GUARANTEES

    def test_no_guarantees_returns_empty(self):
        assert _claim_coverage([], [{"plan_index": 0, "outcome": "pass"}]) == {}
        assert _claim_coverage(
            [{"guarantee": "", "check_index": 0}],
            [{"plan_index": 0, "outcome": "pass"}]) == {}


class TestBehaviorPreservation:
    """A plan WITHOUT stated_guarantees parses byte-identically to
    today — no coverage, no block, no new keys anywhere."""

    def test_absent_key_is_todays_behavior(self, tmp_path):
        import runs as runs_mod
        rd = tmp_path / "run-plain"
        runs_mod.set_current_run_dir(rd)
        plan = {"checks": [{"description": "passes", "command": "true"}]}
        adapter = _adapter(plan, _VERDICT_OK)
        try:
            verdict = verify_goal_completion(
                "build a thing", [], adapter, workspace_path=str(tmp_path))
        finally:
            runs_mod.set_current_run_dir(None)
        assert verdict.claim_coverage == {}
        verdict_msg = adapter.complete.call_args_list[1].args[0][1].content
        assert "Stated-but-unexercised" not in verdict_msg
        rows = [json.loads(l) for l in
                (rd / "build" / "closure_verdicts.jsonl")
                .read_text().splitlines() if l]
        assert "claim_coverage" not in rows[0]


class TestJudgeVisibilityAndAdvisory:
    def test_unwired_block_reaches_verdict_call(self, tmp_path):
        """Unwired guarantees are named to the judge; exercised ones stay
        out of the block (their evidence is the check results)."""
        plan = {
            "checks": [{"description": "passes", "command": "true"}],
            "stated_guarantees": [
                {"guarantee": "responds on port 8080", "check_index": 0},
                {"guarantee": "logs errors to errors.log", "check_index": None},
            ],
        }
        adapter = _adapter(plan, _VERDICT_OK)
        verify_goal_completion(
            "build a thing", [], adapter, workspace_path=str(tmp_path))
        verdict_msg = adapter.complete.call_args_list[1].args[0][1].content
        assert "Stated-but-unexercised guarantees" in verdict_msg
        assert "logs errors to errors.log" in verdict_msg
        _block = verdict_msg.split("Stated-but-unexercised guarantees")[1]
        assert "responds on port 8080" not in _block

    def test_advisory_never_flips_or_recaps(self, tmp_path):
        """v1 posture: with unwired guarantees present and a positive
        judge verdict, no deterministic branch flips complete or touches
        confidence."""
        plan = {
            "checks": [{"description": "passes", "command": "true"}],
            "stated_guarantees": [
                {"guarantee": "caches results", "check_index": None},
            ],
        }
        adapter = _adapter(plan, _VERDICT_OK)
        verdict = verify_goal_completion(
            "build a thing", [], adapter, workspace_path=str(tmp_path))
        assert verdict.complete is True
        assert verdict.confidence == 0.9


class TestEvidenceGateRecording:
    def test_row_and_event_carry_coverage(self, tmp_path):
        """The eval hook must capture the dimension it defers to
        (2026-08-16 claims-audit rule): per-guarantee records + per-status
        counts on BOTH the persisted row and the CLOSURE_VERDICT event —
        that is the firing data the keep/kill gate adjudicates."""
        import runs as runs_mod
        rd = tmp_path / "run-cov"
        runs_mod.set_current_run_dir(rd)
        plan = {
            "checks": [{"description": "passes", "command": "true"}],
            "stated_guarantees": [
                {"guarantee": "responds on port 8080", "check_index": 0},
                {"guarantee": "logs errors to errors.log", "check_index": None},
            ],
        }
        adapter = _adapter(plan, _VERDICT_OK)
        events = []
        try:
            with patch("captains_log.log_event",
                       side_effect=lambda *a, **k: events.append((a, k))):
                verify_goal_completion(
                    "build a thing", [], adapter,
                    workspace_path=str(tmp_path), loop_id="loopcov")
        finally:
            runs_mod.set_current_run_dir(None)
        rows = [json.loads(l) for l in
                (rd / "build" / "closure_verdicts.jsonl")
                .read_text().splitlines() if l]
        cov = rows[0]["claim_coverage"]
        assert cov["counts"] == {"exercised": 1, "contradicted": 0,
                                 "inconclusive": 0, "unwired": 1}
        assert {e["guarantee"]: e["status"] for e in cov["guarantees"]} == {
            "responds on port 8080": "exercised",
            "logs errors to errors.log": "unwired",
        }
        closure_events = [k for a, k in events
                          if k.get("subject") == "closure_verdict"]
        assert closure_events, "CLOSURE_VERDICT event not emitted"
        assert closure_events[0]["context"]["claim_coverage"] == cov
