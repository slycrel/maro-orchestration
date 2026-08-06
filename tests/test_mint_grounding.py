"""Mint-time grounding (docs/MINT_GROUNDING_DESIGN.md slice 1).

The pins here encode the five-specimen claims-vs-events family: an
"authenticated fetch" claim must come back unsupported when the run's
event log holds only unauthenticated fetches (LT-4 B3, LT-5 step 8);
"confirmed this session" with no tying probe stays honest-unprobed
(LT-4 B1w); imperative advice — the dominant lesson shape — is never a
claim at all. The dummy-token pin reproduces a live find: the loose
auth regex marked LT-5's own specimen supported off `token=a` in a
public syndication-CDN URL.
"""

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mint_grounding import (
    collect_run_tool_events,
    extract_claims,
    ground_text,
    ground_lessons_for_run,
    grounding_marker,
    has_unsupported,
)


def _ev(name="Bash", input_="", output="", is_error=False):
    return {"ref": "build/calls/call-00001.json#tool_events[0]",
            "name": name, "input": input_, "output": output,
            "is_error": is_error}


# ---------------------------------------------------------------------------
# claim extraction
# ---------------------------------------------------------------------------

class TestExtractClaims:
    def test_imperative_advice_mints_no_claims(self):
        # The dominant (and desired) lesson shape asserts nothing about
        # what happened — it must never be stamped.
        advice = ("Verify mount state before running the suite; always "
                  "check credentials first. Exact-pricing claims need two "
                  "trusted sources.")
        assert extract_claims(advice) == []

    def test_past_tense_method_assertion_is_a_claim(self):
        claims = extract_claims("The page was fetched via the CDN.")
        assert [c["family"] for c in claims] == ["fetch"]

    def test_auth_and_fetch_claims_mint_separately(self):
        # B3: "authenticated CLI fetch" — the fetch can be supported while
        # the auth half is refuted; one claim per family makes that visible.
        claims = extract_claims("Data was retrieved by an authenticated fetch.")
        assert {c["family"] for c in claims} == {"auth", "fetch"}


# ---------------------------------------------------------------------------
# grounding join
# ---------------------------------------------------------------------------

class TestGroundText:
    def test_auth_claim_unsupported_without_credentials(self):
        # B3 / LT-5 step-8 specimen: unauthenticated fetch events exist,
        # so "fetched" is supported — and "authenticated" is affirmatively
        # unsupported, receipts empty.
        events = [_ev(input_="curl -sL https://r.jina.ai/https://x.com/post",
                      output="rendered page text")]
        stamps = ground_text(
            "Content was retrieved via an authenticated fetch.", events)
        by_family = {s["family"]: s for s in stamps}
        assert by_family["auth"]["status"] == "unsupported"
        assert by_family["auth"]["receipts"] == []
        assert by_family["fetch"]["status"] == "supported"
        assert by_family["fetch"]["receipts"]

    def test_dummy_token_url_is_not_credentials(self):
        # Live find during the build: LT-5's syndication-CDN URL carries
        # `token=a` — a hardcoded public parameter — and a bare-substring
        # auth marker marked the specimen's "authenticated fetch" claim
        # supported. Credential markers must require credential-shaped
        # values.
        events = [_ev(input_="curl -s 'https://cdn.syndication.twimg.com/"
                             "tweet-result?id=123&token=a' -o /tmp/s.json")]
        stamps = ground_text("An authenticated fetch supplied the data.",
                             events)
        auth = [s for s in stamps if s["family"] == "auth"][0]
        assert auth["status"] == "unsupported"

    def test_named_specific_does_not_ride_an_unrelated_receipt(self):
        """Adversarial-review pin R1-1 (2026-08-06): 'fetched from api-a'
        must not be stamped supported with api-b's receipt. A claim naming
        an identifier-shaped specific that ties to NO candidate lands
        unprobed — attaching an unrelated event as its receipt is the exact
        false-support class this module refuses. Red on revert."""
        events = [_ev(input_="curl -s https://api-b.example/other")]
        stamps = ground_text(
            "The report was fetched from api-a.example.", events)
        fetch = [s for s in stamps if s["family"] == "fetch"][0]
        assert fetch["status"] == "unprobed"
        assert fetch["receipts"] == []

    def test_generic_claim_keeps_family_level_support(self):
        """The counterweight: a claim with no identifier-shaped specifics
        ('content was fetched') asserts nothing a family event can't cover
        — family-level support survives the R1-1 tightening."""
        events = [_ev(input_="curl -s https://api-b.example/other")]
        stamps = ground_text("Content was downloaded successfully.", events)
        fetch = [s for s in stamps if s["family"] == "fetch"][0]
        assert fetch["status"] == "supported"

    def test_anonymous_login_url_is_not_credentials(self):
        """Adversarial-review pin R1-2 (2026-08-06): bare login/passw/
        credential words matched an anonymous `curl .../login` URL path —
        same class as the pinned `token=a`. Only assigned-value forms
        (password=..., credential=...) are credential material."""
        events = [_ev(input_="curl -s https://public.example/login")]
        stamps = ground_text(
            "Data was fetched by an authenticated request.", events)
        auth = [s for s in stamps if s["family"] == "auth"][0]
        assert auth["status"] == "unsupported"

    def test_assigned_password_field_still_counts_as_credentials(self):
        events = [_ev(input_="curl -d 'user=x&password=hunter2' "
                             "https://site.example/login")]
        stamps = ground_text(
            "Data was fetched by an authenticated request.", events)
        auth = [s for s in stamps if s["family"] == "auth"][0]
        assert auth["status"] == "supported"

    def test_real_bearer_header_supports_auth_claim(self):
        events = [_ev(input_="curl -H 'Authorization: Bearer abcd1234efgh' "
                             "https://api.example.com/v1/items")]
        stamps = ground_text("Items were fetched from the authenticated API.",
                             events)
        auth = [s for s in stamps if s["family"] == "auth"][0]
        assert auth["status"] == "supported"

    def test_probe_claim_without_tie_is_unprobed(self):
        # B1w shape: probe events exist but none mention what the claim
        # says was confirmed — an unrelated Bash event must NOT support
        # "confirmed"; honest-unprobed, never guessed.
        events = [_ev(input_="ls -la /tmp")]
        stamps = ground_text(
            "Rate-limit blocks were confirmed for the zzqqxx endpoint.",
            events)
        assert stamps[0]["status"] == "unprobed"

    def test_probe_claim_with_keyword_tie_is_supported(self):
        events = [_ev(input_="curl -s https://api.example.com/zzqqxx",
                      output="HTTP 429 rate limited")]
        stamps = ground_text(
            "Rate-limit blocks were confirmed for the zzqqxx endpoint.",
            events)
        assert stamps[0]["status"] == "supported"
        assert stamps[0]["receipts"] == [events[0]["ref"]]

    def test_zero_event_log_makes_claims_unsupported(self):
        # An LLM-only run affirmatively did not probe anything: [] is
        # ground truth, not missing data.
        stamps = ground_text("The fix was verified by probing the endpoint.",
                             [])
        assert stamps[0]["status"] == "unsupported"

    def test_error_events_never_support(self):
        # An attempted fetch is not evidence the fetch happened.
        events = [_ev(name="WebFetch", input_="https://example.com/page",
                      is_error=True)]
        stamps = ground_text("The page content was fetched.", events)
        fetch = [s for s in stamps if s["family"] == "fetch"][0]
        assert fetch["status"] == "unsupported"


# ---------------------------------------------------------------------------
# event collection
# ---------------------------------------------------------------------------

class TestCollectRunToolEvents:
    def test_refs_point_into_call_records(self, tmp_path):
        calls = tmp_path / "build" / "calls"
        calls.mkdir(parents=True)
        (calls / "call-00007.json").write_text(json.dumps({
            "tool_events": [
                {"name": "Bash", "input": "echo hi", "output": "hi",
                 "is_error": False},
                {"name": "Read", "input": "/tmp/x", "output": "text",
                 "is_error": "False"},
            ]}))
        events = collect_run_tool_events(tmp_path)
        assert [e["ref"] for e in events] == [
            "build/calls/call-00007.json#tool_events[0]",
            "build/calls/call-00007.json#tool_events[1]",
        ]
        assert events[0]["is_error"] is False

    def test_missing_calls_dir_is_none_not_empty(self, tmp_path):
        # No ground truth → no stamps (None); a present-but-eventless
        # record set → [] (which IS ground truth). The distinction keeps
        # "we can't know" from masquerading as "nothing happened".
        assert collect_run_tool_events(tmp_path / "nope") is None
        calls = tmp_path / "build" / "calls"
        calls.mkdir(parents=True)
        (calls / "call-00001.json").write_text(json.dumps({"tool_events": []}))
        assert collect_run_tool_events(tmp_path) == []


# ---------------------------------------------------------------------------
# consumer helpers
# ---------------------------------------------------------------------------

class TestMarkers:
    def test_marker_only_for_unsupported(self):
        assert grounding_marker([{"status": "supported", "claim": "x"}]) == ""
        assert grounding_marker([{"status": "unprobed", "claim": "x"}]) == ""
        assert grounding_marker(None) == ""
        m = grounding_marker([{"status": "unsupported",
                               "claim": "authenticated fetch"}])
        assert "mint-grounding" in m and "authenticated fetch" in m

    def test_has_unsupported(self):
        assert has_unsupported([{"status": "unsupported"}])
        assert not has_unsupported([{"status": "supported"}])
        assert not has_unsupported([])
        assert not has_unsupported(None)

    def test_grounding_summary_census_tag(self):
        from mint_grounding import grounding_summary
        assert grounding_summary(None) == ""
        assert grounding_summary([]) == ""
        tag = grounding_summary([
            {"status": "supported"}, {"status": "supported"},
            {"status": "unsupported"}, {"status": "unprobed"}])
        assert tag == " [claims: 2✓/1✗/1?]"


# ---------------------------------------------------------------------------
# store integration — stamps ride the mint into both stores, and the
# injection/seed surfaces weigh them
# ---------------------------------------------------------------------------

class TestMintIntegration:
    def _run_with_events(self, handle_id):
        import runs
        rd = runs.create_run_dir(handle_id, prompt="goal")
        calls = rd / "build" / "calls"
        calls.mkdir(parents=True, exist_ok=True)
        (calls / "call-00001.json").write_text(json.dumps({
            "tool_events": [
                {"name": "Bash",
                 "input": "curl -s https://example.com/dataset.csv",
                 "output": "ok", "is_error": False},
            ]}))
        return rd

    def test_reflect_stamps_both_stores(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        self._run_with_events("h-ground-1")

        class FakeAdapter:
            def complete(self, messages, **kw):
                return types.SimpleNamespace(content=json.dumps([
                    {"lesson": "The dataset was retrieved from example.com "
                               "by an authenticated fetch.",
                     "type": "execution"}]))

        from memory import reflect_and_record, load_lessons
        reflect_and_record(
            "goal", "done", "summary", task_type="general",
            adapter=FakeAdapter(), handle_id="h-ground-1")

        from knowledge_web import load_tiered_lessons, MemoryTier
        tiered = load_tiered_lessons(MemoryTier.MEDIUM, task_type="general",
                                     min_score=0.0)
        assert len(tiered) == 1
        statuses = {g["family"]: g["status"] for g in tiered[0].grounding}
        assert statuses["fetch"] == "supported"
        assert statuses["auth"] == "unsupported"
        fetch_receipts = [g for g in tiered[0].grounding
                          if g["family"] == "fetch"][0]["receipts"]
        assert fetch_receipts == ["build/calls/call-00001.json#tool_events[0]"]

        flat = load_lessons(task_type="general", limit=10)
        assert len(flat) == 1
        assert {g["family"]: g["status"] for g in flat[0].grounding} == statuses

    def test_unresolvable_run_mints_unstamped(self, monkeypatch, tmp_path):
        # Fail-open: no run dir → the mint proceeds exactly as before,
        # rows stampless (absent key on disk, not unsupported-everything).
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        class FakeAdapter:
            def complete(self, messages, **kw):
                return types.SimpleNamespace(content=json.dumps([
                    {"lesson": "The dataset was fetched from example.com.",
                     "type": "execution"}]))

        from memory import reflect_and_record
        reflect_and_record(
            "goal", "done", "summary", task_type="general",
            adapter=FakeAdapter(), handle_id="h-no-such-run")
        from knowledge_web import load_tiered_lessons, MemoryTier
        tiered = load_tiered_lessons(MemoryTier.MEDIUM, task_type="general",
                                     min_score=0.0)
        assert len(tiered) == 1
        assert tiered[0].grounding == []

    def test_injection_surfaces_render_unsupported_marker(self, monkeypatch,
                                                          tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        stamp = [{"claim": "an authenticated fetch supplied the data",
                  "family": "auth", "status": "unsupported",
                  "receipts": [], "note": "no auth-family tool events"}]
        from memory_ledger import _store_lesson
        _store_lesson(task_type="general", outcome="done",
                      lesson="Data came from an authenticated fetch.",
                      source_goal="g", grounding=stamp)
        from memory import inject_lessons_for_task
        flat_out = inject_lessons_for_task("general", "some goal")
        assert "mint-grounding" in flat_out

        from knowledge_web import record_tiered_lesson, inject_tiered_lessons
        record_tiered_lesson(
            lesson_text="Data came from an authenticated fetch.",
            task_type="general", outcome="done", source_goal="g",
            grounding=stamp)
        tiered_out = inject_tiered_lessons("general")
        assert "mint-grounding" in tiered_out

    # (A seed-reader skip test lived here for ~an hour; the S2 seed block
    # itself was removed the same night on the A/B verdict, taking the
    # surface with it — test_mint_form.TestNoSeedExemplar pins the removal.)


def test_ground_lessons_for_run_fail_open_on_bad_ref(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    out = ground_lessons_for_run(["The page was fetched."], "no-such-ref")
    assert out == [[]]
    assert ground_lessons_for_run([], "x") == []
