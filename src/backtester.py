#!/usr/bin/env python3
"""
backtester.py — Replay strategy rules against real Polymarket price timeseries.

Usage:
    python3 src/backtester.py --input <markets.json> --output <trades.jsonl>

Strategies:
  1. buy_at_open_sell_at_close: buy first candle open, sell last candle close
  2. buy_under_0.2_sell_over_0.8: accumulate when price < 0.2, sell when > 0.8

Output per trade: entry_price, exit_price, direction, market_slug, timestamp,
                  strategy, pnl, outcome, settlement_price
"""

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path("/home/clawd/prototypes/poe-orchestration/prototypes/poe-orchestration/projects/find-10-highly-profitable-polymarket")

POSITION_SIZE = 10.0  # USD per trade


def fetch_ohlc(slug: str, outcome: str) -> list:
    """Fetch OHLC timeseries for a market outcome via polymarket-cli."""
    try:
        result = subprocess.run(
            ["python3", "-m", "polymarket_cli", "history",
             "--slug", slug, "--outcome", outcome,
             "--format", "ohlc", "--json"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return data.get("ohlc", [])
    except Exception:
        return []


def strategy_buy_open_sell_close(candles: list, slug: str, outcome: str,
                                  settlement: float, start_ts: int) -> dict | None:
    """Buy first open, sell last close."""
    if len(candles) < 1:
        return None
    entry = candles[0]["open"]
    exit_p = candles[-1]["close"]
    # P&L: binary market — win means settlement=1.0
    if settlement == 1.0:
        pnl = POSITION_SIZE * (exit_p - entry)
    else:
        pnl = -POSITION_SIZE * entry  # lost the position
    return {
        "strategy": "buy_at_open_sell_at_close",
        "market_slug": slug,
        "outcome": outcome,
        "settlement_price": settlement,
        "entry_price": round(entry, 4),
        "exit_price": round(exit_p, 4),
        "direction": "long",
        "timestamp": start_ts,
        "pnl": round(pnl, 4),
    }


def strategy_buy_low_sell_high(candles: list, slug: str, outcome: str,
                                settlement: float, start_ts: int) -> dict | None:
    """Buy when price < 0.2, sell when price > 0.8."""
    bought = None
    for c in candles:
        if bought is None and c["low"] < 0.2:
            bought = c["close"] if c["close"] < 0.2 else 0.18
        if bought is not None and c["high"] > 0.8:
            exit_p = c["open"] if c["open"] > 0.8 else 0.82
            if settlement == 1.0:
                pnl = POSITION_SIZE * (exit_p - bought)
            else:
                pnl = -POSITION_SIZE * bought
            return {
                "strategy": "buy_under_0.2_sell_over_0.8",
                "market_slug": slug,
                "outcome": outcome,
                "settlement_price": settlement,
                "entry_price": round(bought, 4),
                "exit_price": round(exit_p, 4),
                "direction": "long",
                "timestamp": start_ts,
                "pnl": round(pnl, 4),
            }
    # Never crossed 0.8 — exit at settlement
    if bought is not None:
        exit_p = settlement
        pnl = POSITION_SIZE * (exit_p - bought) if settlement == 1.0 else -POSITION_SIZE * bought
        return {
            "strategy": "buy_under_0.2_sell_over_0.8",
            "market_slug": slug,
            "outcome": outcome,
            "settlement_price": settlement,
            "entry_price": round(bought, 4),
            "exit_price": round(exit_p, 4),
            "direction": "long",
            "timestamp": start_ts,
            "pnl": round(pnl, 4),
        }
    return None


def compute_metrics(trades: list) -> dict:
    if not trades:
        return {}
    pnls = [t["pnl"] for t in trades]
    total_pnl = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / len(pnls)
    # Max drawdown
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    # Sharpe (daily, assuming each trade is independent)
    mean = total_pnl / len(pnls)
    variance = sum((p - mean) ** 2 for p in pnls) / len(pnls)
    std = math.sqrt(variance) if variance > 0 else 0.001
    sharpe = (mean / std) * math.sqrt(len(pnls))
    return {
        "total_trades": len(trades),
        "total_pnl": round(total_pnl, 4),
        "win_rate": round(win_rate, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe_ratio": round(sharpe, 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Markets JSON file")
    parser.add_argument("--output", required=True, help="Output JSONL for trades")
    parser.add_argument("--limit", type=int, default=20, help="Max markets to backtest")
    args = parser.parse_args()

    # Resolve input path — check /tmp first, then workspace
    input_path = Path(args.input)
    if not input_path.exists():
        alt = WORKSPACE / input_path.name
        if alt.exists():
            input_path = alt
        else:
            print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
            sys.exit(1)

    # Resolve output path — always write to workspace too
    output_path = Path(args.output)
    workspace_output = WORKSPACE / output_path.name

    with open(input_path) as f:
        markets = json.load(f)

    print(f"Loaded {len(markets)} markets from {input_path}", file=sys.stderr)
    markets = markets[:args.limit]

    all_trades = []
    processed = 0

    for mkt in markets:
        slug = mkt.get("slug", "")
        if not slug:
            continue

        # Find winner and loser outcomes
        odds = mkt.get("odds") or mkt.get("tokens", [])
        if not odds:
            continue

        for token in odds:
            outcome = token.get("outcome", "")
            settlement = token.get("price", 0.0)
            if not outcome:
                continue

            print(f"  Fetching {slug} / {outcome} ...", file=sys.stderr)
            candles = fetch_ohlc(slug, outcome)
            if not candles:
                print(f"    No candles — skipping", file=sys.stderr)
                continue

            start_ts = candles[0].get("windowStart", 0)

            t1 = strategy_buy_open_sell_close(candles, slug, outcome, settlement, start_ts)
            if t1:
                all_trades.append(t1)

            t2 = strategy_buy_low_sell_high(candles, slug, outcome, settlement, start_ts)
            if t2:
                all_trades.append(t2)

        processed += 1
        print(f"  [{processed}/{len(markets)}] {slug} done, trades so far: {len(all_trades)}", file=sys.stderr)

    # Write trades JSONL
    for dest in [workspace_output, output_path]:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w") as f:
            for t in all_trades:
                f.write(json.dumps(t) + "\n")
        print(f"Wrote {len(all_trades)} trades to {dest}", file=sys.stderr)

    # Compute and print metrics
    metrics = compute_metrics(all_trades)
    print(json.dumps(metrics, indent=2))

    # Save metrics
    metrics_path = WORKSPACE / "backtest_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"Metrics saved to {metrics_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
