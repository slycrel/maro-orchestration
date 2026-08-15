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
        """Counts cover EVERY guarantee; only the RECORDED list is capped
        (2026-08-16 review round, Skeptic: capping counts would
        undercount exactly the many-guarantees runs the evidence gate
        most needs). Empty guarantee text is not an entry."""
        raw = [{"guarantee": f"guarantee {i}", "check_index": None}
               for i in range(10)]
        raw.insert(0, {"guarantee": "   ", "check_index": None})
        cov = _claim_coverage(raw, [])
        assert len(cov["guarantees"]) == closure_verify._MAX_STATED_GUARANTEES
        assert cov["overflow"] == 10 - closure_verify._MAX_STATED_GUARANTEES
        assert cov["counts"]["unwired"] == 10

    def test_no_guarantees_returns_empty(self):
        assert _claim_coverage([], [{"plan_index": 0, "outcome": "pass"}]) == {}
        assert _claim_coverage(
            [{"guarantee": "", "check_index": 0}],
            [{"plan_index": 0, "outcome": "pass"}]) == {}

    def test_string_shaped_entries_are_unwired_not_silence(self):
        """Shape tolerance (review round, Architect): a bare-string list —
        plausible cheap-model drift — must record as unwired guarantees,
        not parse to {} like nothing was stated; junk-typed entries and a
        non-list payload count in `malformed` so shape drift is visible
        to the evidence gate instead of biasing it toward kill."""
        cov = _claim_coverage(["warns when config missing", 42],
                              [{"plan_index": 0, "outcome": "pass"}])
        assert cov["counts"]["unwired"] == 1
        assert cov["guarantees"][0]["guarantee"] == "warns when config missing"
        assert cov["malformed"] == 1
        non_list = _claim_coverage("warns when config missing", [])
        assert non_list["malformed"] == 1
        assert non_list["guarantees"] == []
        assert non_list["counts"]["unwired"] == 0


class TestBehaviorPreservation:
    """A plan WITHOUT stated_guarantees parses byte-identically to
    today — no coverage, no block, no new keys anywhere."""

    def test_absent_key_is_todays_behavior(self, tmp_path):
        import runs as runs_mod
        rd = tmp_path / "run-plain"
        runs_mod.set_current_run_dir(rd)
        plan = {"checks": [{"description": "passes", "command": "true"}]}
        adapter = _adapter(plan, _VERDICT_OK)
        events = []
        try:
            with patch("captains_log.log_event",
                       side_effect=lambda *a, **k: events.append((a, k))):
                verdict = verify_goal_completion(
                    "build a thing", [], adapter, workspace_path=str(tmp_path))
        finally:
            runs_mod.set_current_run_dir(None)
        assert verdict.claim_coverage == {}
        # The judge-visible payload is byte-identical to the pre-probe
        # world: no coverage block, and no plan_index leaking into the
        # verification-results JSON (2026-08-16 review round — plan_index
        # is the record's join key, not judge evidence).
        verdict_msg = adapter.complete.call_args_list[1].args[0][1].content
        assert "Stated-guarantee coverage" not in verdict_msg
        assert "plan_index" not in verdict_msg
        rows = [json.loads(l) for l in
                (rd / "build" / "closure_verdicts.jsonl")
                .read_text().splitlines() if l]
        assert "claim_coverage" not in rows[0]
        # The persisted row DOES carry plan_index on executed checks even
        # without guarantees — deliberate additive schema (the join key
        # documents which plan slot each row filled), asserted here so
        # the difference from the judge payload is pinned, not accidental.
        assert rows[0]["check_results"][0]["plan_index"] == 0
        closure_events = [k for a, k in events
                          if k.get("subject") == "closure_verdict"]
        assert closure_events
        assert "claim_coverage" not in closure_events[0]["context"]


class TestJudgeVisibilityAndAdvisory:
    def test_coverage_block_reaches_verdict_call(self, tmp_path):
        """Every recorded mapping is shown WITH its status marker
        (2026-08-16 review round, three-lens convergence: an exercised
        link the judge can't see is a laundering surface — the judge must
        be able to attack the link, not just the outcome)."""
        plan = {
            "checks": [
                {"description": "passes", "command": "true"},
                {"description": "fails", "command": "false"},
            ],
            "stated_guarantees": [
                {"guarantee": "responds on port 8080", "check_index": 0},
                {"guarantee": "retries on failure", "check_index": 1},
                {"guarantee": "logs errors to errors.log", "check_index": None},
            ],
        }
        adapter = _adapter(plan, _VERDICT_OK)
        verify_goal_completion(
            "build a thing", [], adapter, workspace_path=str(tmp_path))
        verdict_msg = adapter.complete.call_args_list[1].args[0][1].content
        assert "Stated-guarantee coverage" in verdict_msg
        _block = verdict_msg.split("Stated-guarantee coverage")[1]
        assert "- [unwired] logs errors to errors.log" in _block
        assert "- [exercised] responds on port 8080" in _block
        assert "- [contradicted] retries on failure" in _block

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

    def test_marked_coverage_gap_does_not_trip_signal1(self, tmp_path):
        """The Signal-1 echo channel (review round HIGH): a judge that
        follows the prompt — recording an unverified guarantee in gaps as
        "Unverified claim: …" — must NOT trigger the deterministic
        behavioral-gap downgrade; the advisory block would otherwise
        manufacture True→False flips out of its own doctrine wording."""
        plan = {
            "checks": [{"description": "passes", "command": "true"}],
            "stated_guarantees": [
                {"guarantee": "caches results", "check_index": None},
            ],
        }
        verdict_data = {
            "complete": True, "confidence": 0.9,
            "gaps": ["Unverified claim: caches results — was not exercised "
                     "by any check"],
            "summary": "Goal achieved.",
        }
        adapter = _adapter(plan, verdict_data)
        verdict = verify_goal_completion(
            "build a thing", [], adapter, workspace_path=str(tmp_path))
        assert verdict.complete is True
        assert verdict.downgrade_reason == ""

    def test_organic_admission_still_trips_signal1(self, tmp_path):
        """Scope pin for the echo filter: an UNMARKED runtime admission in
        gaps fires Signal 1 exactly as before the probe existed — the
        filter suppresses prompt-induced echoes only, never organic
        self-contradictions."""
        plan = {
            "checks": [{"description": "passes", "command": "true"}],
            "stated_guarantees": [
                {"guarantee": "caches results", "check_index": None},
            ],
        }
        verdict_data = {
            "complete": True, "confidence": 0.9,
            "gaps": ["the server was never started"],
            "summary": "Goal achieved.",
        }
        adapter = _adapter(plan, verdict_data)
        verdict = verify_goal_completion(
            "build a thing", [], adapter, workspace_path=str(tmp_path))
        assert verdict.complete is False
        assert "runtime" in verdict.downgrade_reason

    def test_plan_call_caps_pinned(self, tmp_path):
        """max_tokens=1024 and the prompt-side "at most 8" guarantee cap
        exist to keep one over-long JSON response from losing the CHECKS
        array (review round: overflow fails extract_json entirely, a
        regression against the guarantees-free world). Pinned so neither
        can silently regress."""
        plan = {"checks": [{"description": "passes", "command": "true"}]}
        adapter = _adapter(plan, _VERDICT_OK)
        verify_goal_completion(
            "build a thing", [], adapter, workspace_path=str(tmp_path))
        plan_call = adapter.complete.call_args_list[0]
        assert plan_call.kwargs["max_tokens"] == 1024
        assert "List at most 8 guarantees" in plan_call.args[0][0].content

    def test_audit_lanes_receive_coverage_block(self):
        """Both second-opinion audit lanes see the same pattern-5 evidence
        the judge held (review round, Architect: without it the
        negative-audit's "holds strictly more evidence" premise is false,
        and the pass-audit — whose trigger is exactly the all-static
        achieved shape pattern-5 targets — audits blind). Shared
        renderer; must not fork."""
        from closure_verify import (_audit_positive_verdict,
                                    _audit_negative_verdict)
        cov = {"guarantees": [{"guarantee": "caches results",
                               "check_index": None, "status": "unwired"}],
               "counts": {"exercised": 0, "contradicted": 0,
                          "inconclusive": 0, "unwired": 1}}
        for fn, kwargs in (
            (_audit_positive_verdict, {}),
            (_audit_negative_verdict, {"gaps": [], "downgrade_reasons": []}),
        ):
            adapter = MagicMock()
            resp = MagicMock()
            resp.content = json.dumps(
                {"agrees": True, "reason": "ok", "confidence": 0.9})
            adapter.complete.return_value = resp
            fn(goal="g", adapter=adapter, summary="s", check_results=[],
               workspace_path="", claim_coverage=cov, **kwargs)
            msg = adapter.complete.call_args.args[0][1].content
            assert "Stated-guarantee coverage" in msg
            assert "- [unwired] caches results" in msg


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
