"""Boundary pins for the 2026-08-21 caps sweep (truncation-audit tranche 2).

Each fixed site widened a cut from a starved value to a measured, MARKED
clip. These tests exist because the review round found the sweep's fixes
otherwise unpinned: reverting a site to its old bare slice — or
fat-fingering a cap value — passed the whole suite. Behavioral pins where
the seam is clean; source pins (weaker, but regression-visible) where the
site is buried mid-function.

Distributions behind the values (measured on this box's live workspace):
- stuck/block reasons: n=184, median 291, p99 594, max 913 → 600/1000
- claim-probe receipts: n=447, 13% saturated at the old emit cap → 2000
- operator docs GOALS/CONTEXT/SIGNALS.md: 925–1161 chars live → 4000
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"


class _CapturingAdapter:
    def __init__(self, content='["step one", "step two"]'):
        self.system_prompts = []
        self._content = content

    def complete(self, messages, **kwargs):
        for m in messages:
            if getattr(m, "role", "") == "system":
                self.system_prompts.append(getattr(m, "content", ""))

        class _Resp:
            content = self._content
            input_tokens = 10
            output_tokens = 5
        return _Resp()


class TestPlannerOperatorDocs:
    """planner.py: GOALS/CONTEXT/SIGNALS.md ride decompose prompts whole.

    The old [:500] silently starved all three live operator docs
    (925–1161 chars) — the one input the operator writes by hand for
    exactly this prompt.
    """

    def test_long_goals_doc_passes_uncut(self, tmp_path):
        from planner import decompose

        user_dir = tmp_path / "user"
        user_dir.mkdir()
        body = "GOALS-HEAD " + "g" * 1400 + " GOALS-TAIL"
        (user_dir / "GOALS.md").write_text(body)

        adapter = _CapturingAdapter()
        decompose("check the config", adapter, max_steps=4)
        combined = "\n".join(adapter.system_prompts)
        # Old [:500] kept the head and dropped the tail — the tail is the pin.
        assert "GOALS-TAIL" in combined
        assert body in combined

    def test_runaway_doc_is_bounded_and_marked(self, tmp_path):
        from planner import decompose

        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "CONTEXT.md").write_text("C" * 6000)

        adapter = _CapturingAdapter()
        decompose("check the config", adapter, max_steps=4)
        combined = "\n".join(adapter.system_prompts)
        assert "C" * 6000 not in combined            # bounded
        assert "[truncated: first 4000 of 6000 characters]" in combined


class TestBlockedRetryHint:
    """loop_blocked.py: the retry hint carries the real block reason.

    93% of live block reasons exceeded the old [:120] (median 291, max
    913) — the retry was acting on a fraction of WHY it was blocked.
    """

    def test_hint_carries_typical_reason_whole(self):
        from loop_blocked import _handle_blocked_step

        reason = "REASON-HEAD " + "r" * 560 + " REASON-END"  # ~p99 length
        decision = _handle_blocked_step(
            "write the summary file",
            {"status": "blocked", "stuck_reason": reason, "result": ""},
            prior_retries=0,
            adapter=None,
        )
        assert decision.retry
        assert "REASON-END" in decision.hint          # old [:120] dropped it
        assert reason in decision.hint

    def test_runaway_reason_is_bounded_and_marked(self):
        from loop_blocked import _handle_blocked_step

        reason = "X" * 3000
        decision = _handle_blocked_step(
            "write the summary file",
            {"status": "blocked", "stuck_reason": reason, "result": ""},
            prior_retries=0,
            adapter=None,
        )
        assert decision.retry
        assert "X" * 3000 not in decision.hint        # bounded
        assert "[truncated: first 1000 of 3000 characters]" in decision.hint


class TestClaimProbeReceipt:
    """claim_probe.py: the receipt behind a verdict flip is wide + marked.

    The old [:400] capture (and [:300] emit re-cut) censored 13% of live
    receipts at the cap; probe_command is the replay handle and now rides
    whole up to a marked 2000 breaker.
    """

    def test_long_probe_output_survives_past_old_cap(self, tmp_path):
        from claim_probe import probe_contested_claims

        blob = tmp_path / "blob.txt"
        blob.write_text("P" * 1500)
        claims = [{
            "claim": "the blob file is non-empty",
            "verdict": "CONTESTED",
            "reason": "reviewer doubted it",
            "settled_by_command": f"cat {blob}",
        }]
        out = probe_contested_claims(claims)
        preview = out[0]["probe_output_preview"]
        assert "P" * 1500 in preview                  # old [:400] cut this
        assert out[0]["probe_status"] in ("dismissed", "insufficient")

    def test_runaway_probe_output_is_bounded_and_marked(self, tmp_path):
        from claim_probe import probe_contested_claims

        blob = tmp_path / "blob.txt"
        blob.write_text("Q" * 5000)
        claims = [{
            "claim": "the blob file is non-empty",
            "verdict": "CONTESTED",
            "reason": "reviewer doubted it",
            "settled_by_command": f"cat {blob}",
        }]
        out = probe_contested_claims(claims)
        preview = out[0]["probe_output_preview"]
        assert "Q" * 5000 not in preview              # bounded
        assert "[truncated: first 2000 of 5000 characters]" in preview


class TestSourcePins:
    """Source pins for sweep sites buried mid-function (weaker than a
    behavioral pin, but a revert to a bare slice or a fat-fingered cap
    value fails here instead of passing the whole suite silently).
    """

    def _src(self, name: str) -> str:
        return (SRC / name).read_text()

    def test_failure_chain_entries_ride_measured_clip(self):
        s = self._src("loop_blocked.py")
        assert s.count("clip(_br_reason, 600)") == 1
        assert s.count("clip(_decision.metacognitive_reason, 600)") == 1
        assert s.count("clip(_stuck_reason, 600)") == 1

    def test_missing_input_escalation_carries_reason(self):
        s = self._src("loop_blocked.py")
        assert "clip(block_reason, 1000)" in s

    def test_adjudication_failure_summary_clipped_both_ends(self):
        assert "_clip(str(row.get(\"summary\", \"\") or \"\"), 600)" in \
            self._src("memory_ledger.py")
        assert "clip(str(ctx.get(\"failure_summary\") or \"\"), 600)" in \
            self._src("knowledge_lens.py")

    def test_judge_windows_ride_review_step_cut(self):
        inspector = self._src("inspector.py")
        assert inspector.count("_clip(result_summary, _REVIEW_STEP_CUT)") == 1
        assert inspector.count("_clip(summary, _REVIEW_STEP_CUT)") == 1
        assert "clip(getattr(o, \"result\", \"\") or \"\", _REVIEW_STEP_CUT)" \
            in self._src("reanchor.py")

    def test_navigator_signals_lost_the_starved_duplicate(self):
        # The [:80] block_reason copy in the signals dict duplicated the
        # full value already passed as the block_reason parameter.
        s = self._src("loop_blocked.py")
        assert "outcome.get(\"stuck_reason\", \"blocked\")[:80]" not in s
