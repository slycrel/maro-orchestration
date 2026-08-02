"""Run-scoped terrain memory (RUN_TEACHINGS §5b, Jeremy 2026-08-02).

"Should be a simple memory-related thing to remember that certain sites are
blocked rather than churning on learning that over and over."

The measured waste: the cold chlorination run re-attempted the same blocked
archives across ~6 steps (~$15 of $25). These pin the accumulator's
behavior, the conservatism that keeps it from lying, and the loop wiring.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from terrain import TerrainMemory, scan_tool_events  # noqa: E402


def _ev(url, output="", is_error=False, error=""):
    return {"name": "WebFetch", "input": {"url": url},
            "output": output, "is_error": is_error, "error": error}


class TestObservation:
    def test_403_is_recorded_with_host_and_step(self):
        m = TerrainMemory()
        new = scan_tool_events([_ev("https://babel.hathitrust.org/cgi/ls",
                                    output="HTTP 403 Forbidden")], 3, m)
        assert new and "hathitrust.org" in new[0]
        f = m.facts["babel.hathitrust.org"]
        assert f.reason == "403 forbidden"
        assert f.first_step == 3 and f.steps == [3]

    def test_cloudflare_challenge_counts(self):
        m = TerrainMemory()
        scan_tool_events([_ev("https://books.google.com/x",
                              output="Just a moment... Cloudflare")], 1, m)
        assert m.facts["books.google.com"].reason == "cloudflare challenge"

    def test_repeat_hits_accumulate_without_reporting_new(self):
        m = TerrainMemory()
        assert scan_tool_events([_ev("https://a.example/1", output="429")], 1, m)
        # Same host, later step: not "newly" blocked, but evidence deepens.
        assert scan_tool_events([_ev("https://a.example/2", output="429")], 2, m) == []
        f = m.facts["a.example"]
        assert f.hits == 2 and f.steps == [1, 2]

    def test_first_reason_wins(self):
        m = TerrainMemory()
        scan_tool_events([_ev("https://a.example/1", output="403 Forbidden")], 1, m)
        scan_tool_events([_ev("https://a.example/2", output="Cloudflare")], 2, m)
        assert m.facts["a.example"].reason == "403 forbidden"


class TestConservatism:
    """A false 'blocked' belief is worse than none — it would suppress a
    source that actually works. Only hard, unambiguous blocks count."""

    def test_transient_failures_are_not_terrain(self):
        m = TerrainMemory()
        for out in ("HTTP 500 Internal Server Error", "502 Bad Gateway",
                    "503 Service Unavailable", "connection reset by peer",
                    "read timed out"):
            scan_tool_events([_ev("https://flaky.example/x", output=out)], 1, m)
        assert m.facts == {}, "transient failures must remain retryable"

    def test_success_is_not_terrain(self):
        m = TerrainMemory()
        scan_tool_events([_ev("https://ok.example/x",
                              output="HTTP 200 OK — 4kb of json")], 1, m)
        assert m.facts == {}

    def test_host_comes_from_request_not_error_text(self):
        """A URL mentioned in an error body is not the URL that was requested."""
        m = TerrainMemory()
        scan_tool_events([_ev("https://real.example/x",
                              output="403 forbidden; see https://other.example/policy")],
                         1, m)
        assert "real.example" in m.facts and "other.example" not in m.facts

    def test_malformed_events_never_raise(self):
        m = TerrainMemory()
        assert scan_tool_events([None, "junk", {}, {"input": None}], 1, m) == []
        assert scan_tool_events(None, 1, m) == []
        assert m.facts == {}


class TestRender:
    def test_empty_renders_empty(self):
        """Load-bearing: the ledger's contract is that zero contributions
        leave prompts byte-identical."""
        assert TerrainMemory().render() == ""

    def test_render_names_host_reason_and_evidence(self):
        m = TerrainMemory()
        scan_tool_events([_ev("https://a.example/1", output="403")], 2, m)
        out = m.render()
        assert "a.example" in out and "403 forbidden" in out
        assert "do not retry" in out.lower()
        assert "since step 2" in out

    def test_render_is_capped(self):
        m = TerrainMemory()
        for i in range(20):
            scan_tool_events([_ev(f"https://h{i}.example/x", output="403")], 1, m)
        out = m.render()
        assert out.count("  - ") <= 12
        assert "more." in out


class TestPromotion:
    """The evidence gate for a durable terrain teaching (§4): one blocked
    response is a hiccup; the same host across separate steps is a fact."""

    def test_single_step_observation_is_not_promotable(self):
        m = TerrainMemory()
        scan_tool_events([_ev("https://a.example/1", output="403")], 1, m)
        scan_tool_events([_ev("https://a.example/2", output="403")], 1, m)
        assert m.promotable() == [], "same step twice is still one observation"

    def test_across_two_steps_is_promotable(self):
        m = TerrainMemory()
        scan_tool_events([_ev("https://a.example/1", output="403")], 1, m)
        scan_tool_events([_ev("https://a.example/2", output="403")], 4, m)
        assert [f.host for f in m.promotable()] == ["a.example"]


class TestLoopWiring:
    def test_context_carries_a_terrain_memory(self):
        from loop_types import LoopContext
        ctx = LoopContext()
        assert isinstance(ctx.terrain, TerrainMemory)
        assert ctx.terrain.render() == ""

    def test_each_context_gets_its_own(self):
        """A shared default would leak one run's terrain into every other."""
        from loop_types import LoopContext
        a, b = LoopContext(), LoopContext()
        a.terrain.observe("a.example", "403 forbidden", 1)
        assert b.terrain.facts == {}

    def test_terrain_contribution_is_replaced_not_stacked(self):
        """The loop drops+re-appends per step so a re-arm can't replay a
        stale snapshot (same discipline as the `time` contributor)."""
        from loop_types import ContributionLedger
        led = ContributionLedger()
        m = TerrainMemory()
        m.observe("a.example", "403 forbidden", 1)
        for _ in range(3):
            led.drop_source("terrain")
            led.append("terrain", "context", m.render())
        assert len([r for r in led.drain() if r.source == "terrain"]) == 1
