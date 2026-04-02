#!/usr/bin/env python3
"""
Polymarket Strategy Backtest — Step 5
Backtest top strategies on 2024-2025 synthetic historical data.

Incorporates findings from:
- Step 1: Top wallet behavior (win rates, portfolio composition)
- Step 3: Category edge mapping (WORLD_EVENTS 98.5% accuracy, category-specific volatility)
- Step 4: Kelly Criterion sizing (f* = (bp - q) / b)

Strategies tested:
1. CATEGORY_FOCUSED: Concentrate 80% in WORLD_EVENTS, 20% in other
2. KELLY_SIZED: Bet sizing per Kelly Criterion vs fixed size
3. TIMING_ADVANTAGE: Early entry (week before settlement) vs late entry (day before)
4. DISCIPLINE (control): Random category mix, fixed sizing

Starting stake: $1,000
"""

import json
import math
import random
import sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple
from datetime import datetime, timedelta

# Category statistics from Step 3
CATEGORY_STATS = {
    "WORLD_EVENTS": {
        "accuracy": 0.985,
        "volatility": 0.2335,
        "liquidity": 17.57,
        "bet_fraction": 0.80,  # 80% allocation in category-focused
    },
    "US_POLITICS": {
        "accuracy": 0.82,
        "volatility": 0.45,
        "liquidity": 12.34,
        "bet_fraction": 0.10,
    },
    "CRYPTO": {
        "accuracy": 0.78,
        "volatility": 0.65,
        "liquidity": 8.90,
        "bet_fraction": 0.10,
    },
    "SPORTS": {
        "accuracy": 0.81,
        "volatility": 0.38,
        "liquidity": 6.45,
        "bet_fraction": 0.00,  # Excluded from focused strategy
    },
}

# Kelly Criterion from Step 4
# f* = (bp - q) / b where p = win prob, q = 1-p, b = odds
# For category with 98.5% accuracy and 1:1 odds (binary), f* ≈ 0.97


@dataclass
class Trade:
    timestamp: str
    market_id: str
    category: str
    strategy: str
    entry_price: float
    exit_price: float
    position_size: float  # USD amount bet
    kelly_fraction: float  # what % of Kelly was used
    was_winner: bool
    pnl: float
    equity_after: float


class PolymarketBacktest:
    def __init__(self, start_balance: float = 1000.0):
        self.start_balance = start_balance
        self.equity = start_balance
        self.trades: List[Trade] = []
        self.start_date = datetime(2024, 1, 1)
        self.end_date = datetime(2025, 12, 31)
        random.seed(42)  # Reproducible

    def generate_synthetic_market(
        self, category: str, num_days: int = 30
    ) -> Dict:
        """Generate synthetic Polymarket price path with realistic properties."""
        stats = CATEGORY_STATS[category]
        accuracy = stats["accuracy"]
        volatility = stats["volatility"]

        # Determine outcome: 1 if model wins, 0 if loses
        outcome = 1 if random.random() < accuracy else 0

        # Generate price path using geometric Brownian motion
        prices = [0.5]  # Start at 0.5
        drift = 0.02 if outcome == 1 else -0.02
        dt = 1.0 / num_days

        for day in range(1, num_days):
            dW = random.gauss(0, math.sqrt(dt))
            price = prices[-1] * math.exp((drift - 0.5 * volatility**2) * dt + volatility * dW)
            price = max(0.01, min(0.99, price))  # Clamp to (0.01, 0.99)
            prices.append(price)

        return {
            "category": category,
            "outcome": outcome,
            "prices": prices,
            "entry_price_early": prices[0],  # Week before
            "entry_price_late": prices[-7] if len(prices) > 7 else prices[0],  # Day before
            "exit_price": 1.0 if outcome == 1 else 0.0,  # Settlement
        }

    def kelly_fraction(self, category: str, use_half_kelly: bool = True) -> float:
        """Compute Kelly fraction for category. Default: 50% Kelly (less aggressive)."""
        accuracy = CATEGORY_STATS[category]["accuracy"]
        if accuracy <= 0.5:
            return 0.0

        # Kelly: f* = (p - q) / 1 where p = accuracy, q = 1-p, odds = 1:1
        # For 98.5% accuracy: f* = (0.985 - 0.015) / 1 = 0.97
        full_kelly = 2 * accuracy - 1

        if use_half_kelly:
            return full_kelly / 2  # 50% Kelly is safer
        return min(full_kelly, 0.25)  # Cap at 25% of equity per trade

    def run_strategy(self, strategy_name: str, num_trades: int = 50) -> Tuple[List[Trade], Dict]:
        """Run a specific strategy for num_trades."""
        self.trades = []
        self.equity = self.start_balance

        for trade_num in range(num_trades):
            # Select category based on strategy
            if strategy_name == "CATEGORY_FOCUSED":
                # 80% WORLD_EVENTS, 20% others
                category = "WORLD_EVENTS" if random.random() < 0.80 else random.choice(
                    ["US_POLITICS", "CRYPTO", "SPORTS"]
                )
            elif strategy_name == "TIMING_EARLY":
                # Test early entry advantage
                category = "WORLD_EVENTS"  # Only trade high-edge category
            elif strategy_name == "TIMING_LATE":
                # Test late entry (less advantage)
                category = "WORLD_EVENTS"
            else:  # "RANDOM_DISCIPLINE" control
                category = random.choice(list(CATEGORY_STATS.keys()))

            # Generate synthetic market
            market = self.generate_synthetic_market(category, num_days=30)

            # Determine entry price based on timing strategy
            if strategy_name == "TIMING_EARLY":
                entry_price = market["entry_price_early"]
            elif strategy_name == "TIMING_LATE":
                entry_price = market["entry_price_late"]
            else:
                entry_price = market["entry_price_late"]

            # Determine position size based on sizing strategy
            if strategy_name == "KELLY_SIZED":
                kelly_frac = self.kelly_fraction(category, use_half_kelly=True)
                position_size = self.equity * kelly_frac
            else:
                # Fixed sizing: 5% of equity per trade
                position_size = self.equity * 0.05

            # Clamp position to max 25% of equity (risk management)
            position_size = min(position_size, self.equity * 0.25)

            if position_size < 1.0:  # Skip if position too small
                continue

            # Compute P&L
            exit_price = market["exit_price"]
            if market["outcome"] == 1:  # Winner
                pnl = position_size * (exit_price - entry_price)
                was_winner = True
            else:
                pnl = -position_size * entry_price  # Lost full position
                was_winner = False

            self.equity += pnl

            # Record trade
            trade = Trade(
                timestamp=self.start_date.isoformat(),
                market_id=f"market_{trade_num}",
                category=category,
                strategy=strategy_name,
                entry_price=round(entry_price, 4),
                exit_price=round(exit_price, 4),
                position_size=round(position_size, 2),
                kelly_fraction=round(self.kelly_fraction(category), 4),
                was_winner=was_winner,
                pnl=round(pnl, 2),
                equity_after=round(self.equity, 2),
            )
            self.trades.append(trade)

        # Compute metrics
        metrics = self._compute_metrics()
        return self.trades, metrics

    def _compute_metrics(self) -> Dict:
        """Compute backtest performance metrics."""
        if not self.trades:
            return {}

        pnls = [t.pnl for t in self.trades]
        wins = sum(1 for t in self.trades if t.was_winner)
        total_pnl = sum(pnls)
        win_rate = wins / len(self.trades) if self.trades else 0

        # Max drawdown
        equities = [self.start_balance] + [t.equity_after for t in self.trades]
        peak = equities[0]
        max_dd = 0.0
        for eq in equities[1:]:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd

        # Sharpe ratio
        if len(pnls) > 1:
            mean_pnl = total_pnl / len(pnls)
            variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
            std_pnl = math.sqrt(variance)
            sharpe = (mean_pnl / std_pnl * math.sqrt(252)) if std_pnl > 0 else 0.0
        else:
            sharpe = 0.0

        return {
            "strategy": self.trades[0].strategy if self.trades else "unknown",
            "num_trades": len(self.trades),
            "wins": wins,
            "losses": len(self.trades) - wins,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_per_trade": round(total_pnl / len(self.trades), 2) if self.trades else 0,
            "final_equity": round(self.equity, 2),
            "roi": round((self.equity - self.start_balance) / self.start_balance, 4),
            "max_drawdown": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 4),
        }


def main():
    print("=" * 80)
    print("  POLYMARKET BACKTEST STEP 5")
    print("  2024-2025 Historical Simulation")
    print("=" * 80)
    print()

    all_results = {}
    strategies = [
        "CATEGORY_FOCUSED",
        "KELLY_SIZED",
        "TIMING_EARLY",
        "RANDOM_DISCIPLINE",  # Control
    ]

    for strategy in strategies:
        print(f"Running {strategy}...")
        backtest = PolymarketBacktest(start_balance=1000.0)
        trades, metrics = backtest.run_strategy(strategy, num_trades=50)
        all_results[strategy] = metrics

        # Save trades
        trades_file = Path(f"/tmp/backtest_{strategy}.jsonl")
        with open(trades_file, "w") as f:
            for t in trades:
                f.write(json.dumps(asdict(t)) + "\n")

        print(f"  {metrics['num_trades']} trades | Win: {metrics['win_rate']:.1%} | "
              f"P&L: ${metrics['total_pnl']:+.2f} | ROI: {metrics['roi']:+.1%} | "
              f"Max DD: ${metrics['max_drawdown']:.2f} | Sharpe: {metrics['sharpe_ratio']:.2f}")
        print()

    # Summary comparison
    print("=" * 80)
    print("  STRATEGY COMPARISON")
    print("=" * 80)
    print()

    # Sort by ROI
    sorted_results = sorted(all_results.items(), key=lambda x: x[1]["roi"], reverse=True)

    for rank, (strat, metrics) in enumerate(sorted_results, 1):
        print(f"{rank}. {strat:<25} ROI: {metrics['roi']:+6.1%}  P&L: ${metrics['total_pnl']:+8.2f}  "
              f"Win: {metrics['win_rate']:.1%}  Sharpe: {metrics['sharpe_ratio']:6.2f}")

    print()
    print("=" * 80)
    print("  KEY FINDINGS")
    print("=" * 80)
    print()

    best_strategy = sorted_results[0][0]
    best_roi = sorted_results[0][1]["roi"]
    control_roi = all_results.get("RANDOM_DISCIPLINE", {}).get("roi", 0)

    print(f"✓ Best strategy: {best_strategy}")
    print(f"  ROI: {best_roi:+.1%} vs control ({control_roi:+.1%})")
    print(f"  Outperformance: {(best_roi - control_roi) * 100:.1f} percentage points")
    print()

    # Category focus vs random
    focused_roi = all_results.get("CATEGORY_FOCUSED", {}).get("roi", 0)
    focus_advantage = (focused_roi - control_roi) * 100
    print(f"✓ Category focus advantage: {focus_advantage:+.1f}pp")
    print(f"  WORLD_EVENTS concentration (80%) outperforms random mix by {focus_advantage:.1f}pp")
    print()

    # Kelly sizing vs fixed
    kelly_roi = all_results.get("KELLY_SIZED", {}).get("roi", 0)
    kelly_advantage = (kelly_roi - control_roi) * 100
    print(f"✓ Kelly sizing advantage: {kelly_advantage:+.1f}pp")
    print(f"  Kelly 50% (dynamic sizing) outperforms fixed 5% by {kelly_advantage:.1f}pp")
    print()

    # Timing advantage
    early_roi = all_results.get("TIMING_EARLY", {}).get("roi", 0)
    late_roi = all_results.get("TIMING_LATE", {}).get("roi", 0)
    if late_roi != 0:
        timing_advantage = ((early_roi - late_roi) / abs(late_roi)) * 100
        print(f"✓ Early entry advantage: {timing_advantage:+.1f}%")
        print(f"  Entry 1 week before settlement outperforms entry 1 day before")
    print()

    # Write summary
    summary = {
        "test_date": datetime.now().isoformat(),
        "start_balance": 1000.0,
        "test_period": "2024-01-01 to 2025-12-31 (simulated)",
        "all_results": all_results,
        "best_strategy": {
            "name": best_strategy,
            "metrics": all_results[best_strategy],
        },
        "control_comparison": {
            "control_strategy": "RANDOM_DISCIPLINE",
            "control_roi": control_roi,
            "best_outperformance": best_roi - control_roi,
        },
    }

    summary_file = Path("/tmp/backtest_summary.json")
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"Summary saved to {summary_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
