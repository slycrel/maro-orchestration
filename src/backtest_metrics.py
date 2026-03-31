#!/usr/bin/env python3
"""backtest_metrics.py — read backtest_trades.jsonl, compute aggregate metrics, print summary."""

import json
import math
import sys
from pathlib import Path

DEFAULT_INPUT = Path("/home/clawd/prototypes/poe-orchestration/prototypes/poe-orchestration/projects/find-10-highly-profitable-polymarket/backtest_trades.jsonl")


def load_trades(path: Path) -> list[dict]:
    trades = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                trades.append(json.loads(line))
    return trades


def compute_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {}

    pnls = [t["pnl"] for t in trades]
    total_pnl = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / len(pnls)

    # Max drawdown on cumulative P&L curve
    cumulative = []
    running = 0.0
    for p in pnls:
        running += p
        cumulative.append(running)

    peak = cumulative[0]
    max_dd = 0.0
    for val in cumulative:
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio (annualized, assuming 1 trade/day)
    n = len(pnls)
    mean_pnl = total_pnl / n
    if n > 1:
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1)
        std_pnl = math.sqrt(variance)
        sharpe = (mean_pnl / std_pnl * math.sqrt(252)) if std_pnl > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "total_trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(win_rate, 4),
        "total_pnl": round(total_pnl, 4),
        "avg_pnl_per_trade": round(mean_pnl, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe_ratio": round(sharpe, 4),
    }


def per_strategy_breakdown(trades: list[dict]) -> dict[str, dict]:
    by_strategy: dict[str, list[float]] = {}
    for t in trades:
        s = t.get("strategy", "unknown")
        by_strategy.setdefault(s, []).append(t["pnl"])
    result = {}
    for s, pnls in by_strategy.items():
        result[s] = {
            "trades": len(pnls),
            "total_pnl": round(sum(pnls), 4),
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
        }
    return result


def print_summary(metrics: dict, by_strategy: dict[str, dict]) -> None:
    print("=" * 55)
    print("  BACKTEST METRICS SUMMARY")
    print("=" * 55)
    print(f"  Total trades      : {metrics['total_trades']}")
    print(f"  Wins / Losses     : {metrics['wins']} / {metrics['losses']}")
    print(f"  Win rate          : {metrics['win_rate']:.1%}")
    print(f"  Total P&L         : {metrics['total_pnl']:+.4f}")
    print(f"  Avg P&L / trade   : {metrics['avg_pnl_per_trade']:+.4f}")
    print(f"  Max drawdown      : {metrics['max_drawdown']:.4f}")
    print(f"  Sharpe ratio      : {metrics['sharpe_ratio']:.4f}")
    print("-" * 55)
    print("  Per-strategy breakdown:")
    for s, m in by_strategy.items():
        print(f"    {s:<35} trades={m['trades']:3d}  pnl={m['total_pnl']:+8.4f}  win={m['win_rate']:.1%}")
    print("=" * 55)


def main():
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    if not input_path.exists():
        # fallback to /tmp
        fallback = Path("/tmp/backtest_trades.jsonl")
        if fallback.exists():
            input_path = fallback
        else:
            print(f"ERROR: {input_path} not found and /tmp/backtest_trades.jsonl missing", file=sys.stderr)
            sys.exit(1)

    trades = load_trades(input_path)
    if not trades:
        print("ERROR: no trades loaded", file=sys.stderr)
        sys.exit(1)

    metrics = compute_metrics(trades)
    by_strategy = per_strategy_breakdown(trades)
    print_summary(metrics, by_strategy)

    # Also write JSON summary alongside input
    out_json = input_path.parent / "backtest_metrics.json"
    with open(out_json, "w") as f:
        json.dump({"metrics": metrics, "by_strategy": by_strategy}, f, indent=2)
    print(f"\n  JSON saved: {out_json}")


if __name__ == "__main__":
    main()
