"""Tests for polymarket.py — read-only Polymarket CLI wrapper."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# _run helper
# ---------------------------------------------------------------------------

class TestRun:
    def _call(self, args, stdout="", rc=0):
        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = rc
        mock_result.stdout = stdout
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result) as m:
            from polymarket import _run
            return _run(args)

    def test_returns_stdout_on_success(self):
        result = self._call(["list"], stdout="hello")
        assert result == "hello"

    def test_raises_on_nonzero_exit(self):
        from polymarket import _run
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error message"
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="exit 1"):
                _run(["list"])

    def test_raises_on_file_not_found(self):
        import subprocess
        with patch("subprocess.run", side_effect=FileNotFoundError):
            from polymarket import _run
            with pytest.raises(RuntimeError, match="not found"):
                _run(["list"])

    def test_raises_on_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
            from polymarket import _run
            with pytest.raises(RuntimeError, match="timed out"):
                _run(["list"])


# ---------------------------------------------------------------------------
# polymarket_search
# ---------------------------------------------------------------------------

class TestPolymarketSearch:
    def _fake_run(self, data):
        return patch("polymarket._run", return_value=json.dumps(data))

    def test_returns_json_string(self):
        markets = [{"question": "Will AI beat humans at chess?", "slug": "ai-chess"}]
        with self._fake_run(markets):
            from polymarket import polymarket_search
            result = polymarket_search("AI chess")
        parsed = json.loads(result)
        assert parsed == markets

    def test_passes_limit_and_sort(self):
        calls = []
        def fake_run(args):
            calls.append(args)
            return "[]"
        with patch("polymarket._run", side_effect=fake_run):
            from polymarket import polymarket_search
            polymarket_search("test", limit=5, sort="liquidity")
        assert "--limit" in calls[0]
        assert "5" in calls[0]
        assert "--sort" in calls[0]
        assert "liquidity" in calls[0]

    def test_active_only_flag(self):
        calls = []
        def fake_run(args):
            calls.append(args)
            return "[]"
        with patch("polymarket._run", side_effect=fake_run):
            from polymarket import polymarket_search
            polymarket_search("test", active_only=True)
        assert "--active-only" in calls[0]

    def test_error_returns_json_error_object(self):
        with patch("polymarket._run", side_effect=RuntimeError("cli failed")):
            from polymarket import polymarket_search
            result = polymarket_search("test")
        parsed = json.loads(result)
        assert "error" in parsed
        assert parsed["query"] == "test"


# ---------------------------------------------------------------------------
# polymarket_list
# ---------------------------------------------------------------------------

class TestPolymarketList:
    def test_returns_json_string(self):
        with patch("polymarket._run", return_value="[]"):
            from polymarket import polymarket_list
            result = polymarket_list()
        assert result == "[]"

    def test_min_liquidity_arg(self):
        calls = []
        def fake_run(args):
            calls.append(args)
            return "[]"
        with patch("polymarket._run", side_effect=fake_run):
            from polymarket import polymarket_list
            polymarket_list(min_liquidity=1000.0)
        assert "--min-liquidity" in calls[0]
        assert "1000.0" in calls[0]

    def test_error_returns_json_error_object(self):
        with patch("polymarket._run", side_effect=RuntimeError("cli failed")):
            from polymarket import polymarket_list
            result = polymarket_list()
        parsed = json.loads(result)
        assert "error" in parsed


# ---------------------------------------------------------------------------
# polymarket_market
# ---------------------------------------------------------------------------

class TestPolymarketMarket:
    def test_returns_json_string(self):
        market = {"slug": "will-trump-win", "question": "Will Trump win?"}
        with patch("polymarket._run", return_value=json.dumps(market)):
            from polymarket import polymarket_market
            result = polymarket_market("will-trump-win")
        assert json.loads(result) == market

    def test_passes_slug(self):
        calls = []
        def fake_run(args):
            calls.append(args)
            return "{}"
        with patch("polymarket._run", side_effect=fake_run):
            from polymarket import polymarket_market
            polymarket_market("my-slug")
        assert "--slug" in calls[0]
        assert "my-slug" in calls[0]

    def test_error_returns_json_error_object(self):
        with patch("polymarket._run", side_effect=RuntimeError("not found")):
            from polymarket import polymarket_market
            result = polymarket_market("bad-slug")
        parsed = json.loads(result)
        assert "error" in parsed
        assert parsed["slug"] == "bad-slug"


# ---------------------------------------------------------------------------
# polymarket_price / polymarket_midpoint / polymarket_history / polymarket_trades
# ---------------------------------------------------------------------------

class TestPolymarketPrice:
    def test_returns_json_string(self):
        with patch("polymarket._run", return_value='{"price": 0.65}'):
            from polymarket import polymarket_price
            result = polymarket_price("tok123")
        assert json.loads(result)["price"] == 0.65

    def test_error_returns_error_object(self):
        with patch("polymarket._run", side_effect=RuntimeError("bad")):
            from polymarket import polymarket_price
            result = polymarket_price("tok")
        assert "error" in json.loads(result)


class TestPolymarketMidpoint:
    def test_returns_json_string(self):
        with patch("polymarket._run", return_value='{"mid": 0.50}'):
            from polymarket import polymarket_midpoint
            result = polymarket_midpoint("tok123")
        assert json.loads(result)["mid"] == 0.50

    def test_error_returns_error_object(self):
        with patch("polymarket._run", side_effect=RuntimeError("bad")):
            from polymarket import polymarket_midpoint
            result = polymarket_midpoint("tok")
        assert "error" in json.loads(result)


class TestPolymarketHistory:
    def test_passes_interval_and_fidelity(self):
        calls = []
        def fake_run(args):
            calls.append(args)
            return "[]"
        with patch("polymarket._run", side_effect=fake_run):
            from polymarket import polymarket_history
            polymarket_history("tok", interval="1d", fidelity=30)
        assert "--interval" in calls[0]
        assert "1d" in calls[0]
        assert "--fidelity" in calls[0]
        assert "30" in calls[0]

    def test_error_returns_error_object(self):
        with patch("polymarket._run", side_effect=RuntimeError("bad")):
            from polymarket import polymarket_history
            result = polymarket_history("tok")
        assert "error" in json.loads(result)


class TestPolymarketTrades:
    def test_passes_limit(self):
        calls = []
        def fake_run(args):
            calls.append(args)
            return "[]"
        with patch("polymarket._run", side_effect=fake_run):
            from polymarket import polymarket_trades
            polymarket_trades("tok", limit=5)
        assert "--limit" in calls[0]
        assert "5" in calls[0]

    def test_error_returns_error_object(self):
        with patch("polymarket._run", side_effect=RuntimeError("bad")):
            from polymarket import polymarket_trades
            result = polymarket_trades("tok")
        assert "error" in json.loads(result)


# ---------------------------------------------------------------------------
# polymarket_health_check
# ---------------------------------------------------------------------------

class TestPolymarketHealthCheck:
    def test_available_when_cli_exists(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            from polymarket import polymarket_health_check
            status = polymarket_health_check()
        assert status["available"] is True
        assert "functions" in status
        assert len(status["functions"]) > 0

    def test_unavailable_when_cli_missing(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            from polymarket import polymarket_health_check
            status = polymarket_health_check()
        assert status["available"] is False

    def test_unavailable_on_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            from polymarket import polymarket_health_check
            status = polymarket_health_check()
        assert status["available"] is False
