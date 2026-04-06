"""Polymarket CLI integration — read-only market data for research agents.

Wraps polymarket-cli subcommands (search, list, market, midpoint, price,
history, trades) with structured JSON output. No wallet required.

Tool functions registered in step_exec.py EXECUTE_TOOLS_WORKER:
  - polymarket_search(query, limit=10) -> JSON string
  - polymarket_list(sort="volume24hr", limit=10, active_only=True) -> JSON string
  - polymarket_market(slug) -> JSON string
  - polymarket_price(token_id) -> JSON string
  - polymarket_midpoint(token_id) -> JSON string

Usage from agent steps:
    polymarket_search("AI regulation 2025")
    polymarket_market("will-trump-win-2024")
    polymarket_price("<token_id>")
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from typing import Any, Dict, Optional

log = logging.getLogger("poe.polymarket")

_CLI = "polymarket-cli"
_DEFAULT_LIMIT = 10
_TIMEOUT_S = 30


def _run(args: list[str]) -> str:
    """Run polymarket-cli with the given args. Returns stdout as string.

    Raises RuntimeError on non-zero exit.
    """
    cmd = [_CLI] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT_S
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"polymarket-cli not found — install with: pip install polymarket-cli"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"polymarket-cli timed out after {_TIMEOUT_S}s")

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[:400]
        raise RuntimeError(f"polymarket-cli exit {result.returncode}: {err}")

    return result.stdout.strip()


def polymarket_search(
    query: str,
    *,
    limit: int = _DEFAULT_LIMIT,
    active_only: bool = True,
    sort: str = "volume24hr",
) -> str:
    """Search Polymarket markets by keyword.

    Args:
        query:       Keyword or phrase to search for.
        limit:       Max markets to return (default 10).
        active_only: If True, only return active markets (default True).
        sort:        Sort field: volume24hr | volume | liquidity | endDate | competitive.

    Returns:
        JSON string — list of market objects with question, slug, volume, probability.
    """
    args = ["search", query, "--limit", str(limit), "--sort", sort, "--json"]
    if active_only:
        args.append("--active-only")
    try:
        raw = _run(args)
        # Validate it's JSON before returning
        json.loads(raw)
        return raw
    except (RuntimeError, json.JSONDecodeError) as e:
        return json.dumps({"error": str(e), "query": query})


def polymarket_list(
    *,
    limit: int = _DEFAULT_LIMIT,
    sort: str = "volume24hr",
    active_only: bool = True,
    min_liquidity: Optional[float] = None,
    min_volume24hr: Optional[float] = None,
) -> str:
    """List top Polymarket markets.

    Args:
        limit:         Max markets to return (default 10).
        sort:          Sort field: volume24hr | volume | liquidity | endDate | competitive.
        active_only:   If True, only return active markets.
        min_liquidity: Minimum liquidity filter (USD).
        min_volume24hr: Minimum 24h volume filter (USD).

    Returns:
        JSON string — list of market objects.
    """
    args = ["list", "--limit", str(limit), "--sort", sort, "--json"]
    if active_only:
        args.append("--active-only")
    if min_liquidity is not None:
        args += ["--min-liquidity", str(min_liquidity)]
    if min_volume24hr is not None:
        args += ["--min-volume24hr", str(min_volume24hr)]
    try:
        raw = _run(args)
        json.loads(raw)
        return raw
    except (RuntimeError, json.JSONDecodeError) as e:
        return json.dumps({"error": str(e)})


def polymarket_market(slug: str) -> str:
    """Fetch details for a specific market by slug.

    Args:
        slug: Polymarket market slug (e.g. "will-trump-win-2024").

    Returns:
        JSON string — market detail object with question, outcomes, volume, odds.
    """
    args = ["market", "--slug", slug, "--json"]
    try:
        raw = _run(args)
        json.loads(raw)
        return raw
    except (RuntimeError, json.JSONDecodeError) as e:
        return json.dumps({"error": str(e), "slug": slug})


def polymarket_midpoint(token_id: str) -> str:
    """Fetch the current midpoint price for a market token.

    Args:
        token_id: CLOB token ID (from market outcomes).

    Returns:
        JSON string — {"token_id": ..., "mid": <float>} or error.
    """
    args = ["midpoint", token_id, "--json"]
    try:
        raw = _run(args)
        json.loads(raw)
        return raw
    except (RuntimeError, json.JSONDecodeError) as e:
        return json.dumps({"error": str(e), "token_id": token_id})


def polymarket_price(token_id: str) -> str:
    """Fetch the last trade price for a market token.

    Args:
        token_id: CLOB token ID (from market outcomes).

    Returns:
        JSON string — {"token_id": ..., "price": <float>} or error.
    """
    args = ["price", token_id, "--json"]
    try:
        raw = _run(args)
        json.loads(raw)
        return raw
    except (RuntimeError, json.JSONDecodeError) as e:
        return json.dumps({"error": str(e), "token_id": token_id})


def polymarket_history(
    token_id: str,
    *,
    interval: str = "1h",
    fidelity: int = 60,
) -> str:
    """Fetch price history for a market token.

    Args:
        token_id: CLOB token ID.
        interval: Time interval (e.g. "1h", "1d", "1w", "all").
        fidelity: Resolution in minutes (default 60).

    Returns:
        JSON string — list of {t: timestamp, p: price} objects.
    """
    args = ["history", token_id, "--interval", interval,
            "--fidelity", str(fidelity), "--json"]
    try:
        raw = _run(args)
        json.loads(raw)
        return raw
    except (RuntimeError, json.JSONDecodeError) as e:
        return json.dumps({"error": str(e), "token_id": token_id})


def polymarket_trades(token_id: str, *, limit: int = 20) -> str:
    """Fetch recent public trades for a market token.

    Args:
        token_id: CLOB token ID.
        limit:    Max trades to return (default 20).

    Returns:
        JSON string — list of trade objects.
    """
    args = ["trades", token_id, "--limit", str(limit), "--json"]
    try:
        raw = _run(args)
        json.loads(raw)
        return raw
    except (RuntimeError, json.JSONDecodeError) as e:
        return json.dumps({"error": str(e), "token_id": token_id})


# ---------------------------------------------------------------------------
# poe-doctor health check
# ---------------------------------------------------------------------------

def polymarket_health_check() -> Dict[str, Any]:
    """Return health status for polymarket-cli availability."""
    try:
        result = subprocess.run(
            [_CLI, "--help"], capture_output=True, text=True, timeout=5
        )
        available = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        available = False

    return {
        "tool": "polymarket-cli",
        "available": available,
        "functions": [
            "polymarket_search",
            "polymarket_list",
            "polymarket_market",
            "polymarket_midpoint",
            "polymarket_price",
            "polymarket_history",
            "polymarket_trades",
        ],
    }


# ---------------------------------------------------------------------------
# CLI for quick testing
# ---------------------------------------------------------------------------

def _main(argv: Optional[list] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Polymarket read-only data tool")
    sub = p.add_subparsers(dest="cmd")

    s_search = sub.add_parser("search", help="Search markets")
    s_search.add_argument("query")
    s_search.add_argument("--limit", type=int, default=5)
    s_search.add_argument("--all", action="store_true", dest="all_markets")

    s_list = sub.add_parser("list", help="List top markets")
    s_list.add_argument("--limit", type=int, default=5)
    s_list.add_argument("--sort", default="volume24hr")

    s_market = sub.add_parser("market", help="Get market by slug")
    s_market.add_argument("slug")

    s_price = sub.add_parser("price", help="Get token price")
    s_price.add_argument("token_id")

    s_mid = sub.add_parser("mid", help="Get token midpoint")
    s_mid.add_argument("token_id")

    args = p.parse_args(argv)

    if args.cmd == "search":
        print(polymarket_search(args.query, limit=args.limit,
                                active_only=not args.all_markets))
    elif args.cmd == "list":
        print(polymarket_list(limit=args.limit, sort=args.sort))
    elif args.cmd == "market":
        print(polymarket_market(args.slug))
    elif args.cmd == "price":
        print(polymarket_price(args.token_id))
    elif args.cmd == "mid":
        print(polymarket_midpoint(args.token_id))
    else:
        p.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(_main())
