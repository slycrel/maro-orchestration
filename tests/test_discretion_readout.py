"""Tests for discretion_readout — the chunk-7 judgement report.

All section computers are pure functions over event lists, so these tests
pass synthetic events directly (hermetic — no workspace reads). The loader
gets its own tmp-dir test covering the rotated-archive glob.
"""

import json

import discretion_readout as dr


def _ev(event_type, context=None, *, summary="", timestamp="2026-07-22T10:00:00+00:00"):
    return {"event_type": event_type, "timestamp": timestamp,
            "summary": summary, "context": context or {}}


class TestMetacogSummary:
    def test_actions_and_evidence_free_retries(self):
        events = [
            _ev("METACOGNITIVE_DECISION",
                {"action": "retry", "retries": 2,
                 "fingerprints": ["a", "b", "b"]}),   # same fp retried
            _ev("METACOGNITIVE_DECISION",
                {"action": "retry", "retries": 1,
                 "fingerprints": ["a", "b"]}),        # new evidence
            _ev("METACOGNITIVE_DECISION",
                {"action": "redecompose", "replan_count": 2,
                 "fingerprints": []}),
            _ev("METACOGNITIVE_DECISION", {"action": "budget_bump"}),
        ]
        s = dr.metacog_summary(events)
        assert s["rows"] == 4
        assert s["by_action"] == {"retry": 2, "redecompose": 1, "budget_bump": 1}
        assert s["evidence_free_retries"] == 1
        assert s["redecompose_rows"] == 1
        assert s["max_replan_count"] == 2

    def test_empty(self):
        s = dr.metacog_summary([])
        assert s["rows"] == 0
        assert s["evidence_free_retries"] == 0


class TestReinjectionSummary:
    def test_volume_and_duplicate_goals(self):
        events = [
            _ev("RECALL_PERFORMED",
                {"knowledge_blocks": 4, "lessons_cited": ["a", "b"],
                 "elapsed_ms": 100, "goal_preview": "Fix the  widget"},
                summary="recall slice=loop: 1 prior attempts, thread chain 0."),
            _ev("RECALL_PERFORMED",
                {"knowledge_blocks": 2, "lessons_cited": [],
                 "elapsed_ms": 300, "goal_preview": "fix the widget"},
                summary="recall slice=loop: 0 prior attempts, thread chain 0."),
            _ev("RECALL_PERFORMED",
                {"knowledge_blocks": 0, "goal_preview": "different goal"},
                summary="recall slice=dispatch: 0 prior attempts, thread chain 0."),
        ]
        s = dr.reinjection_summary(events)
        assert s["rows"] == 3
        assert s["avg_knowledge_blocks"] == 2.0
        assert s["by_slice"] == {"loop": 2, "dispatch": 1}
        # whitespace/case-normalized exact dup counted once
        assert s["duplicate_goal_texts"] == 1
        assert s["top_duplicates"][0][0] == 2
        assert s["avg_elapsed_ms"] == 200


class TestGateFamilySummary:
    def test_second_family_agreement_rate(self):
        # the live vocabulary carries the SECOND_FAMILY_ prefix
        # (quality_gate.py:720-731) — pin the normalization
        events = [
            _ev("QUALITY_GATE_SECOND_FAMILY", {"decision": "SECOND_FAMILY_AGREE"}),
            _ev("QUALITY_GATE_SECOND_FAMILY", {"decision": "SECOND_FAMILY_AGREE"}),
            _ev("QUALITY_GATE_SECOND_FAMILY", {"decision": "SECOND_FAMILY_DISSENT"}),
            _ev("QUALITY_GATE_SECOND_FAMILY",
                {"decision": "SECOND_FAMILY_NO_VERDICT"}),
        ]
        s = dr.gate_family_summary(events)["second_family"]
        assert s["rows"] == 4
        # undecided rows stay in the denominator table but not the rate
        assert s["by_decision"]["NO_VERDICT"] == 1
        assert s["agreement_rate"] == round(2 / 3, 3)

    def test_council_seats_and_finding_codes(self):
        events = [
            _ev("QUALITY_GATE_COUNCIL",
                {"seats": [
                    {"lens": "probe", "verdict": "WEAK",
                     "finding_codes": ["PHANTOM_SYMBOL"]},
                    {"lens": "artifact", "verdict": "ACCEPTABLE",
                     "finding_codes": []},
                ],
                 "free_seats": [
                    {"lens": "probe", "verdict": "WEAK",
                     "finding_codes": ["PHANTOM_SYMBOL", "CITATION_INVERSION"]},
                ]},
                summary="weak=1/2 free_flag_unconfirmed"),
        ]
        s = dr.gate_family_summary(events)["council"]
        assert s["rows"] == 1
        assert s["seats"] == 3
        assert s["weak_seats"] == 2
        assert s["free_flag_unconfirmed"] == 1
        assert s["finding_codes"] == {"PHANTOM_SYMBOL": 2, "CITATION_INVERSION": 1}

    def test_cross_ref_zero_claim_rows_kept(self):
        events = [
            _ev("QUALITY_GATE_CROSS_REF",
                {"lane": "hosted_free", "claims_extracted": 3,
                 "claims_checked": 3, "disputes": 1}),
            _ev("QUALITY_GATE_CROSS_REF",
                {"lane": "hosted_free", "claims_extracted": 0,
                 "claims_checked": 0, "disputes": 0}),
        ]
        s = dr.gate_family_summary(events)["cross_ref"]
        assert s["rows"] == 2
        assert s["zero_claim_rows"] == 1
        assert s["claims_checked"] == 3
        assert s["disputes"] == 1


class TestNoveltySummary:
    def test_buckets_boosted_and_pre_chunk6(self):
        events = [
            _ev("LESSON_RECORDED", {"novelty": 0.9, "score": 1.27}),
            _ev("LESSON_RECORDED", {"novelty": 0.1, "score": 1.03}),
            _ev("LESSON_RECORDED", {"novelty": 0.5, "score": 1.0}),
            _ev("LESSON_RECORDED", {"tier": "medium"}),   # pre-chunk-6 row
        ]
        s = dr.novelty_summary(events)
        assert s["rows"] == 4
        assert s["with_novelty"] == 3
        assert s["pre_chunk6_rows"] == 1
        assert s["boosted"] == 2
        assert s["buckets"]["0.75-1.00"] == 1
        assert s["buckets"]["0.00-0.25"] == 1
        assert s["buckets"]["0.50-0.75"] == 1
        assert s["mean_novelty"] == 0.5


class TestDutyCycle:
    def test_per_lane_days(self):
        events = [
            _ev("MEMORY_CONSOLIDATED", {}, timestamp="2026-07-20T01:00:00+00:00"),
            _ev("MEMORY_CONSOLIDATED", {}, timestamp="2026-07-21T01:00:00+00:00"),
            _ev("MEMORY_CONSOLIDATED", {}, timestamp="2026-07-21T09:00:00+00:00"),
            _ev("PLAYBOOK_CURATED", {}, timestamp="2026-07-21T01:00:00+00:00"),
        ]
        s = dr.duty_cycle_summary(events)
        assert s["consolidation"]["rows"] == 3
        assert s["consolidation"]["days_active"] == 2
        assert s["consolidation"]["last"] == "2026-07-21"
        assert s["playbook_curation"]["rows"] == 1

    def test_never_ran_lane_is_visible(self):
        s = dr.duty_cycle_summary([])
        assert s["playbook_curation"]["first"] is None


class TestEffortSummary:
    def test_per_day_grouping_and_totals(self):
        entries = [
            {"recorded_at": "2026-07-21T10:00:00+00:00", "total_tokens": 100,
             "cost_usd": 0.01, "model": "claude-sonnet"},
            {"recorded_at": "2026-07-21T11:00:00+00:00", "total_tokens": 300,
             "cost_usd": 0.03, "model": "claude-haiku"},
            {"recorded_at": "2026-07-22T09:00:00+00:00", "total_tokens": 50,
             "cost_usd": 0.0, "model": "claude-sonnet"},
        ]
        s = dr.effort_summary(entries)
        assert s["total_calls"] == 3
        assert s["total_tokens"] == 450
        assert s["days"]["2026-07-21"]["calls"] == 2
        assert s["days"]["2026-07-21"]["models"] == {
            "claude-sonnet": 1, "claude-haiku": 1}
        assert s["total_cost_usd"] == 0.04


class TestReportAndLoader:
    def test_report_sections_and_honesty_block(self):
        events = [
            _ev("METACOGNITIVE_DECISION", {"action": "retry",
                                           "fingerprints": ["x", "x"]}),
            _ev("RECALL_PERFORMED", {"knowledge_blocks": 1,
                                     "goal_preview": "g"},
                summary="recall slice=loop: 0 prior attempts, thread chain 0."),
            _ev("LESSON_RECORDED", {"novelty": 1.0, "score": 1.3}),
        ]
        payload = dr.build_payload(events=events, step_entries=[
            {"recorded_at": "2026-07-22T09:00:00+00:00", "total_tokens": 10,
             "cost_usd": 0.0, "model": "m"}])
        report = dr.build_report(payload)
        assert "EFFORT" in report
        # EFFORT is the headline; dollars ride as a labeled column, never first
        assert report.index("EFFORT") < report.index("$")
        assert "Not computable today" in report
        assert "fan-out justification" in report
        assert "NOW-triage" in report

    def test_loader_reads_rotated_archives_and_filters(self, tmp_path):
        active = tmp_path / "captains_log.jsonl"
        rotated = tmp_path / "captains_log.20260601-000000.jsonl"
        rotated.write_text(json.dumps(
            _ev("LESSON_RECORDED", {"novelty": 0.5})) + "\n",
            encoding="utf-8")
        active.write_text(
            json.dumps(_ev("RECALL_PERFORMED", {"knowledge_blocks": 2})) + "\n"
            + json.dumps(_ev("NAVIGATOR_DECIDED", {})) + "\n"   # not consumed
            + "not-json\n",
            encoding="utf-8")
        events = dr.load_events(tmp_path)
        types = sorted(e["event_type"] for e in events)
        assert types == ["LESSON_RECORDED", "RECALL_PERFORMED"]

    def test_cli_json_mode(self, tmp_path, capsys):
        (tmp_path / "captains_log.jsonl").write_text(
            json.dumps(_ev("LESSON_RECORDED", {"novelty": 0.5, "score": 1.1}))
            + "\n", encoding="utf-8")
        rc = dr.main(["--log-dir", str(tmp_path), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["novelty"]["with_novelty"] == 1
        assert "not_computable" in payload
