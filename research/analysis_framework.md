# Polymarket Strategy Research — Analysis Framework

**Purpose:** Structured approach to analyzing Polymarket trader behavior and market inefficiencies
**Based on:** Tetlock's superforecasting methodology + market microstructure theory
**Author:** Poe Research System
**Updated:** 2026-03-31

---

## Phase 1: Population Analysis (Weeks 1-2)

### 1.1 Leaderboard Cohort Analysis

**Goal:** Understand the distribution of trader performance and identify subgroups

**Data Sources:**
- Daily leaderboard snapshots (historical)
- Win rate distributions
- P&L ranges

**Analysis Tasks:**

```python
import pandas as pd
import numpy as np
from scipy import stats

# Load leaderboard snapshots
leaderboard_df = pd.read_json('data/snapshots/leaderboard_latest.json')

# Cohort analysis
print("Performance Cohorts:")
print(f"  Top 1%: {leaderboard_df.quantile(0.99)['pnl_30d_change']}")
print(f"  Top 10%: {leaderboard_df.quantile(0.90)['pnl_30d_change']}")
print(f"  Median: {leaderboard_df.quantile(0.50)['pnl_30d_change']}")
print(f"  Bottom 50%: {leaderboard_df.quantile(0.50)['pnl_30d_change']}")

# Win rate distribution
print("\nWin Rate Distribution:")
print(leaderboard_df['win_rate'].describe())

# Correlation: Win rate vs P&L
correlation = leaderboard_df['win_rate'].corr(leaderboard_df['pnl_30d_change'])
print(f"\nCorrelation (win_rate, pnl): {correlation:.3f}")

# Identify outliers (potential data quality issues)
outliers = leaderboard_df[leaderboard_df['win_rate'] > 0.75]
print(f"\nHigh win rate outliers (>75%): {len(outliers)}")
```

**Expected Findings:**
- [ ] Top performers show win rates 55-65% (consistent with GJP results)
- [ ] Small percentage (1-2%) of traders with exceptional returns
- [ ] Weak correlation between win rate and absolute P&L (position sizing matters)
- [ ] Survivorship bias (inactive traders drop off leaderboard)

**Output:** `analysis/01_cohort_distribution.csv`

---

### 1.2 Trader Longevity & Experience Tiers

**Goal:** Segment traders by tenure and test experience effect

**Data Sources:**
- First appearance on leaderboard (inferred from snapshots)
- Historical presence/absence
- Current activity status

**Analysis Tasks:**

```python
# Classify traders by tenure
def tenure_tier(first_appearance_date):
    days_active = (today - first_appearance_date).days
    if days_active < 30: return "new"
    elif days_active < 90: return "emerging"
    elif days_active < 365: return "established"
    else: return "veteran"

leaderboard_df['tenure'] = leaderboard_df['first_appearance'].apply(tenure_tier)

# Compare performance across tiers
performance_by_tenure = leaderboard_df.groupby('tenure')['pnl_30d_change'].describe()
print(performance_by_tenure)

# Win rate by tier
win_rate_by_tenure = leaderboard_df.groupby('tenure')['win_rate'].mean()
print(f"Win rate by tenure: \n{win_rate_by_tenure}")

# Statistical test: Do veterans outperform newcomers?
veterans = leaderboard_df[leaderboard_df['tenure'] == 'veteran']['pnl_30d_change']
newcomers = leaderboard_df[leaderboard_df['tenure'] == 'new']['pnl_30d_change']
t_stat, p_value = stats.ttest_ind(veterans, newcomers)
print(f"T-test (veteran vs new): t={t_stat:.2f}, p={p_value:.4f}")
```

**Expected Findings:**
- [ ] Veteran traders show higher average P&L
- [ ] Survivor bias: inactive traders removed from leaderboard
- [ ] Learning curve evident in first 30-90 days
- [ ] Top performers maintain rank over multiple quarters

**Output:** `analysis/02_tenure_analysis.csv`

---

### 1.3 Specialization & Category Focus

**Goal:** Test whether traders specialize or diversify across markets

**Data Sources:**
- Open positions per trader (API/leaderboard data)
- Market categories (crypto, politics, sports, etc.)
- Concentration ratios

**Analysis Tasks:**

```python
# Load trader positions (if available via API)
positions_df = load_trader_positions()

# Calculate Herfindahl index (specialization measure)
def herfindahl_concentration(position_values):
    total = position_values.sum()
    shares = position_values / total
    return (shares ** 2).sum()

positions_df['concentration'] = positions_df.groupby('trader').apply(
    lambda x: herfindahl_concentration(x['position_value'])
)

# Category distribution
category_focus = positions_df.groupby(['trader', 'category']).size().unstack(fill_value=0)
category_entropy = stats.entropy(category_focus.iloc[0])
print(f"Category entropy (0=specialist, higher=diversified): {category_entropy:.2f}")

# Hypothesis: Specialists have higher win rates in their category
specialists = positions_df[positions_df['concentration'] > 0.6]
generalists = positions_df[positions_df['concentration'] < 0.3]
print(f"Specialist win rate: {specialists['win_rate'].mean():.3f}")
print(f"Generalist win rate: {generalists['win_rate'].mean():.3f}")
```

**Expected Findings:**
- [ ] Profitable traders show moderate specialization (Herfindahl 0.2-0.4)
- [ ] Category experts outperform in their domain
- [ ] Extreme specialization correlates with lower returns (luck vs skill)
- [ ] Crypto and geopolitical markets show different specialization patterns

**Output:** `analysis/03_specialization_patterns.csv`

---

## Phase 2: Trade-Level Analysis (Weeks 2-4)

### 2.1 Entry Timing & Market Conditions

**Goal:** Identify when top traders enter positions relative to market price movement

**Data Sources:**
- Trade history (entry dates, prices)
- Market price history
- Event calendars

**Analysis Tasks:**

```python
# Load trade records and market history
trades = pd.read_csv('data/trades_sample.csv')
market_history = load_market_prices()

# For each trade, compute entry position in market lifecycle
def entry_position(entry_date, market_open_date, market_close_date):
    total_days = (market_close_date - market_open_date).days
    days_elapsed = (entry_date - market_open_date).days
    return days_elapsed / total_days  # 0 = start, 1 = end

trades['entry_position'] = trades.apply(
    lambda x: entry_position(x['entry_date'], x['market_open'], x['market_close']),
    axis=1
)

# Profitability by entry timing
trades['profitable'] = (trades['exit_price'] - trades['entry_price']) * trades['side'] > 0
profitability_by_timing = trades.groupby(
    pd.cut(trades['entry_position'], bins=5)
)['profitable'].mean()
print("Profitability by entry phase:")
print(profitability_by_timing)

# Entry price relative to market range
trades['entry_percentile'] = (trades['entry_price'] - trades['market_min']) / \
                              (trades['market_max'] - trades['market_min'])
print(f"Average entry percentile: {trades['entry_percentile'].mean():.2f}")
```

**Expected Findings:**
- [ ] Superforecasters enter early (0.2-0.4 market lifecycle) to gain information
- [ ] Amateur traders cluster at obvious points (news events)
- [ ] Profitable traders avoid extreme market conditions
- [ ] Entry timing correlates with position size (confidence signal)

**Output:** `analysis/04_entry_timing_analysis.csv`

---

### 2.2 Position Sizing & Confidence Calibration

**Goal:** Test if position sizing reflects true forecasting confidence (calibration)

**Data Sources:**
- Trade sizes
- Win rates per trader
- Outcome resolution data

**Analysis Tasks:**

```python
# Load trades with outcomes
trades = pd.read_csv('data/trades_resolved.csv')

# Bin trades by size (proxy for confidence)
trades['size_quartile'] = pd.qcut(trades['position_size'], q=4, labels=['small', 'med', 'large', 'huge'])

# Profitability by size
profitability_by_size = trades.groupby('size_quartile').agg({
    'profitable': ['mean', 'std', 'count'],
    'position_size': 'mean'
})
print("Profitability by position size:")
print(profitability_by_size)

# Calculate calibration score (Brier Score variant)
def calibration_error(position_size, outcome, forecast_confidence=None):
    # Position size as proxy for confidence
    normalized_size = position_size / position_size.max()
    brier = np.mean((normalized_size - outcome) ** 2)
    return brier

calibration_by_trader = trades.groupby('trader').apply(
    lambda x: calibration_error(x['position_size'].values, x['outcome'].values)
)

print("\nTop 10 traders by calibration quality:")
print(calibration_by_trader.nsmallest(10))

# Test: Are large positions more likely to win?
large_trades = trades[trades['size_quartile'] == 'huge']['profitable'].mean()
small_trades = trades[trades['size_quartile'] == 'small']['profitable'].mean()
chi2, p_val = stats.chi2_contingency(pd.crosstab(trades['size_quartile'], trades['profitable']))[:2]
print(f"\nChi-squared test (position size vs outcome): χ²={chi2:.2f}, p={p_val:.4f}")
```

**Expected Findings:**
- [ ] Well-calibrated traders: position size ∝ win probability
- [ ] Amateur traders: no correlation (random sizing)
- [ ] Superforecasters: high win rate on large positions, low on small positions
- [ ] Kelly Criterion visible in best performers (sizing ∝ edge)

**Output:** `analysis/05_position_sizing_calibration.csv`

---

### 2.3 Hold Duration & Time-to-Resolution

**Goal:** Understand if traders hold until resolution or exit early

**Data Sources:**
- Entry/exit dates
- Market resolution dates
- Exit prices vs resolution prices

**Analysis Tasks:**

```python
# Calculate holding periods
trades['hold_days'] = (trades['exit_date'] - trades['entry_date']).dt.days
trades['days_to_resolution'] = (trades['market_resolution'] - trades['entry_date']).dt.days
trades['exit_ratio'] = trades['hold_days'] / trades['days_to_resolution']

# Holding pattern analysis
print("Hold duration statistics:")
print(trades['hold_days'].describe())

# Exit before resolution?
early_exits = trades[trades['exit_ratio'] < 0.9]
held_to_end = trades[trades['exit_ratio'] >= 0.9]

print(f"\nTraders holding to resolution: {(len(held_to_end)/len(trades)*100):.1f}%")
print(f"Early exit profitability: {early_exits['profitable'].mean():.3f}")
print(f"Hold-to-end profitability: {held_to_end['profitable'].mean():.3f}")

# Hypothesis: Do early exits indicate loss aversion (stop loss) or profit taking?
early_exit_pnl = early_exits['pnl_percent'].describe()
held_pnl = held_to_end['pnl_percent'].describe()

print(f"\nEarly exit avg return: {early_exit_pnl['mean']:.2f}%")
print(f"Held-to-end avg return: {held_pnl['mean']:.2f}%")
```

**Expected Findings:**
- [ ] Superforecasters hold 70-90% of positions to resolution
- [ ] Early exits concentrated in losing trades (loss aversion)
- [ ] Quick exits on winners (profit taking)
- [ ] Time-based strategies (exit at probability milestone) visible in some traders

**Output:** `analysis/06_holding_patterns.csv`

---

## Phase 3: Market Inefficiency Analysis (Weeks 4-5)

### 3.1 Favorite-Longshot Bias Detection

**Goal:** Test if Polymarket exhibits the classic favorite-longshot bias

**Data Sources:**
- Market prices over time
- Market outcomes
- Trade volume by price level

**Analysis Tasks:**

```python
# Load market outcomes and historical prices
markets = pd.read_csv('data/resolved_markets.csv')

# Group by closing price (proxy for market probability)
markets['price_bucket'] = pd.cut(markets['closing_price'], bins=[0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0])

# Compare market price (expected probability) vs actual outcome frequency
accuracy_by_bucket = markets.groupby('price_bucket').agg({
    'resolved_price': 'mean',  # What actually happened (0 or 1)
    'closing_price': 'mean',    # What market predicted
    'count': 'size'
}).rename(columns={'resolved_price': 'actual_frequency', 'closing_price': 'market_prob'})

accuracy_by_bucket['calibration_error'] = \
    abs(accuracy_by_bucket['actual_frequency'] - accuracy_by_bucket['market_prob'])

print("Calibration by price level:")
print(accuracy_by_bucket)

# Longshot bias: Do outcomes <10% probability occur more often than market expects?
longshot_markets = markets[markets['closing_price'] < 0.1]
longshot_actual = longshot_markets['outcome'].mean()
print(f"\nLongshot bias (< 0.1 price):")
print(f"  Market expected: {0.1}")
print(f"  Actual frequency: {longshot_actual:.4f}")
print(f"  Bias: {(longshot_actual - 0.1)*100:.1f} percentage points")

# Profitable betting strategy: exploit favorite-longshot bias
def exploit_flb_strategy(markets):
    """Bet against market consensus where bias is largest"""
    # Buy underpriced longshots (actual > market)
    longshot_opportunities = markets[
        (markets['closing_price'] < 0.2) & 
        (markets['actual_frequency'] > markets['closing_price'] + 0.05)
    ]
    
    # Short overpriced favorites (actual < market)
    favorite_opportunities = markets[
        (markets['closing_price'] > 0.8) & 
        (markets['actual_frequency'] < markets['closing_price'] - 0.05)
    ]
    
    return len(longshot_opportunities), len(favorite_opportunities)

longshot_opps, favorite_opps = exploit_flb_strategy(markets)
print(f"\nExploitable opportunities found:")
print(f"  Underpriced longshots: {longshot_opps}")
print(f"  Overpriced favorites: {favorite_opps}")
```

**Expected Findings:**
- [ ] Longshots (p<0.2) occur more often than market prices suggest (positive bias)
- [ ] Favorites (p>0.8) occur less often than market prices suggest (negative bias)
- [ ] Bias magnitude: 0.5-2% mispricing exploitable with sufficient volume
- [ ] Bias strongest in low-volume markets

**Output:** `analysis/07_favorite_longshot_bias.csv`

---

### 3.2 Sentiment Persistence & Momentum

**Goal:** Detect short-term momentum effects (pricing lags fundamental information)

**Data Sources:**
- Price time series (15-minute or 1-hour granularity)
- Trade volume
- Order flow direction
- News/event timeline

**Analysis Tasks:**

```python
# Load high-frequency price data
prices = pd.read_csv('data/market_prices_hourly.csv')
prices['timestamp'] = pd.to_datetime(prices['timestamp'])

# Calculate price momentum (short-term)
prices['returns_1h'] = prices['price'].pct_change(1)
prices['returns_4h'] = prices['price'].pct_change(4)
prices['returns_24h'] = prices['price'].pct_change(24)

# Test momentum persistence
# Does today's return predict tomorrow's?
autocorr_1h = prices['returns_1h'].autocorr(lag=1)
autocorr_4h = prices['returns_4h'].autocorr(lag=1)
autocorr_24h = prices['returns_24h'].autocorr(lag=1)

print("Momentum autocorrelation (positive = momentum, negative = mean reversion):")
print(f"  1-hour: {autocorr_1h:.3f}")
print(f"  4-hour: {autocorr_4h:.3f}")
print(f"  24-hour: {autocorr_24h:.3f}")

# Strategy: Buy after positive momentum
prices['momentum_signal'] = (prices['returns_4h'] > 0.01).astype(int)
prices['next_return'] = prices['returns_4h'].shift(-1)

momentum_strategy = prices.groupby('momentum_signal')['next_return'].mean()
print(f"\nMomentum strategy P&L:")
print(f"  Buy signal (momentum>0): {momentum_strategy[1]:.4f}")
print(f"  No signal: {momentum_strategy[0]:.4f}")
print(f"  Strategy edge: {(momentum_strategy[1] - momentum_strategy[0])*100:.2f} bps")

# Identify fade opportunities (reverse momentum)
prices['counter_momentum'] = -(prices['returns_4h'] > 0.01).astype(int)
prices['counter_next_return'] = prices['returns_4h'].shift(-1)
fade_pnl = (prices['counter_momentum'] * prices['counter_next_return']).mean()
print(f"\nMean reversion (fade) strategy P&L: {fade_pnl*100:.2f} bps")
```

**Expected Findings:**
- [ ] Short-term positive autocorrelation (momentum effect in 4-24 hour window)
- [ ] Momentum effect decays quickly (exploitable in hours, not days)
- [ ] Larger in low-volume/thin markets
- [ ] Related to information cascade behavior

**Output:** `analysis/08_momentum_analysis.csv`

---

### 3.3 Liquidity & Spread Dynamics

**Goal:** Test if liquid markets are more efficient; identify liquidity-provision edge

**Data Sources:**
- Bid-ask spreads
- Order book depth
- Volume by price level
- Market volatility

**Analysis Tasks:**

```python
# Load order book data
orderbook = pd.read_csv('data/orderbook_snapshots.csv')

# Calculate spread metrics
orderbook['spread_abs'] = orderbook['ask'] - orderbook['bid']
orderbook['spread_pct'] = orderbook['spread_abs'] / orderbook['mid_price']
orderbook['depth_imbalance'] = (orderbook['ask_size'] - orderbook['bid_size']) / \
                                (orderbook['ask_size'] + orderbook['bid_size'])

# Correlation: Spread vs volume
volume_spread = orderbook.groupby(pd.cut(orderbook['volume'], bins=5)).agg({
    'spread_pct': 'mean',
    'volume': 'mean'
})
print("Spread vs Volume:")
print(volume_spread)

# Correlation: Spread vs volatility
volatility = calculate_realized_vol(orderbook['price'])
orderbook['volatility'] = volatility
spread_vol_corr = orderbook['spread_pct'].corr(orderbook['volatility'])
print(f"\nSpread-Volatility correlation: {spread_vol_corr:.3f}")

# Market maker edge: Buy at bid, sell at ask
mm_pnl = (orderbook['spread_pct'] / 2) * orderbook['volume']
print(f"\nMarket maker daily P&L (if making 50% of volume): ${mm_pnl.sum():.0f}")

# Identify times of abnormal spreads (trading opportunities)
spread_zscore = (orderbook['spread_pct'] - orderbook['spread_pct'].mean()) / orderbook['spread_pct'].std()
high_spread_periods = orderbook[spread_zscore > 2]
print(f"\nPeriods with abnormally wide spreads: {len(high_spread_periods)}")
```

**Expected Findings:**
- [ ] Spreads 0.5-2% in liquid markets, 2-5% in thin markets
- [ ] Tight correlation between volume and spread width
- [ ] Volatility spikes widen spreads (adverse selection)
- [ ] Maker-taker edge 0.25-0.5% per round trip

**Output:** `analysis/09_liquidity_spread_analysis.csv`

---

## Phase 4: Winning Strategy Synthesis (Week 5)

### 4.1 Superforecaster Profile Extraction

**Goal:** Identify trader characteristics that predict success

**Data Sources:**
- Top traders (by percentile)
- Bottom performers
- Demographic/visible information

**Analysis Tasks:**

```python
# Define "superforecasters" on Polymarket
top_performers = leaderboard_df[leaderboard_df['pnl_30d_change'] > leaderboard_df['pnl_30d_change'].quantile(0.95)]
bottom_performers = leaderboard_df[leaderboard_df['pnl_30d_change'] < leaderboard_df['pnl_30d_change'].quantile(0.05)]

# Compare characteristics
print("SUPERFORECASTER PROFILE (vs bottom performers):")
print(f"Win Rate: {top_performers['win_rate'].mean():.3f} vs {bottom_performers['win_rate'].mean():.3f}")
print(f"Avg Position Size: {top_performers['avg_position'].mean():.2f} vs {bottom_performers['avg_position'].mean():.2f}")
print(f"Portfolio Concentration: {top_performers['concentration'].mean():.2f} vs {bottom_performers['concentration'].mean():.2f}")
print(f"Market Diversity: {top_performers['categories'].mean():.1f} vs {bottom_performers['categories'].mean():.1f}")
print(f"Hold Duration: {top_performers['hold_days'].mean():.0f}d vs {bottom_performers['hold_days'].mean():.0f}d")

# Statistical significance
vars_to_test = ['win_rate', 'avg_position', 'concentration']
for var in vars_to_test:
    t_stat, p_val = stats.ttest_ind(top_performers[var].dropna(), 
                                      bottom_performers[var].dropna())
    sig = "***" if p_val < 0.001 else "**" if p_val < 0.01 else "*" if p_val < 0.05 else "ns"
    print(f"{var}: t={t_stat:.2f}, p={p_val:.4f} {sig}")
```

**Expected Findings:**
- [ ] Win rates 55-65% (skill edge ~10 percentage points)
- [ ] Moderate position sizing (not random, not all-in)
- [ ] Balanced portfolio concentration (Herfindahl 0.15-0.35)
- [ ] Diverse market participation (8-15 categories active)
- [ ] Longer hold durations (70-90% to resolution)

**Output:** `analysis/10_superforecaster_profile.csv`

---

### 4.2 Actionable Trading Rules

**Goal:** Distill market analysis into rules for position sizing and entry/exit

**Trading Rule Candidates:**

```
RULE 1: Market Efficiency-Based Sizing
IF closing_price is well-calibrated (within Brier score threshold)
THEN size = base_position * (confidence_level / market_price_std)
ELSE reduce size to 50% of base

RULE 2: Favorite-Longshot Bias Exploitation
IF market_price < 0.15 AND historical_frequency > market_price + 0.05
THEN long position at 1.5x base size
ELSE IF market_price > 0.85 AND historical_frequency < market_price - 0.05
THEN short position at 1.5x base size
ELSE normal sizing

RULE 3: Momentum Fade
IF returns_4h > +1% AND (market_cap < threshold OR volatility > 20%)
THEN buy 0.5x; DO NOT chase momentum
ELSE IF returns_4h < -1% in early market phase
THEN consider counter-momentum entry at base size

RULE 4: Liquidity-Aware Execution
IF spread_pct < 0.5% THEN use market orders
ELSE IF spread_pct > 2% THEN use limit orders (wait for fills)
ELSE IF spread_pct > 5% THEN reduce position size by 50%

RULE 5: Portfolio Rebalancing
Weekly: Review top-5 losing positions
IF loss_magnitude > 20% AND days_held < 7 THEN exit
ELSE IF loss_magnitude > 20% AND thesis_still_valid THEN hold
OTHERWISE rebalance toward base allocation
```

**Output:** `analysis/11_trading_rules_template.md`

---

## Phase 5: Validation & Backtesting (Week 6)

### 5.1 Strategy Backtesting Framework

```python
def backtest_strategy(trades_df, rules):
    """
    Simulate trading with discovered rules on historical data
    Inputs:
      - trades_df: historical trades with entry/exit/outcome
      - rules: list of rules to apply
    Outputs:
      - cumulative P&L
      - Sharpe ratio
      - Win rate
      - Max drawdown
    """
    
    # Apply rules to entry signals
    trades_df['signal'] = apply_rules(trades_df, rules)
    
    # Filter to rule-generated trades only
    rule_trades = trades_df[trades_df['signal'] > 0]
    
    # Calculate P&L
    rule_trades['pnl'] = (rule_trades['exit_price'] - rule_trades['entry_price']) * \
                          rule_trades['side'] * rule_trades['position_size']
    
    # Performance metrics
    cumulative_pnl = rule_trades['pnl'].cumsum()
    sharpe = (rule_trades['pnl'].mean() / rule_trades['pnl'].std()) * np.sqrt(252)
    win_rate = (rule_trades['pnl'] > 0).mean()
    max_dd = (cumulative_pnl.expanding().max() - cumulative_pnl).max()
    
    return {
        'cumulative_pnl': cumulative_pnl[-1],
        'sharpe': sharpe,
        'win_rate': win_rate,
        'max_drawdown': max_dd,
        'trades_count': len(rule_trades)
    }
```

**Output:** `analysis/12_backtest_results.csv`

---

## Summary & Deliverables

### Files Generated:
- [ ] `analysis/01_cohort_distribution.csv` — Population stats
- [ ] `analysis/02_tenure_analysis.csv` — Experience effect
- [ ] `analysis/03_specialization_patterns.csv` — Category focus
- [ ] `analysis/04_entry_timing_analysis.csv` — When to enter
- [ ] `analysis/05_position_sizing_calibration.csv` — How much to risk
- [ ] `analysis/06_holding_patterns.csv` — When to exit
- [ ] `analysis/07_favorite_longshot_bias.csv` — Market bias quantification
- [ ] `analysis/08_momentum_analysis.csv` — Short-term price trends
- [ ] `analysis/09_liquidity_spread_analysis.csv` — Execution impact
- [ ] `analysis/10_superforecaster_profile.csv` — Winning trader profile
- [ ] `analysis/11_trading_rules_template.md` — Actionable rules
- [ ] `analysis/12_backtest_results.csv` — Strategy validation

### Expected Insights:

**On Superforecasters:**
- Win rates 55-65%, correlated with position sizing discipline
- Balanced portfolios (Herfindahl 0.20-0.35)
- Hold 70-90% of positions to resolution
- Tenure effect: veterans outperform by 20-50%

**On Market Inefficiencies:**
- Favorite-longshot bias: 0.5-1.5 percentage points exploitable
- Momentum effect: positive autocorrelation in 4-24h window
- Liquidity provision edge: 0.25-0.5% per round-trip
- Spread dynamics: inverse relationship with volume

**On Winning Strategies:**
1. **Entry Timing:** Early (0.2-0.4 market lifecycle), after information gathering
2. **Position Sizing:** ∝ confidence level × market liquidity × portfolio size
3. **Holding:** Until resolution or clear thesis invalidation (not momentum chasing)
4. **Exit:** Loss aversion (exit losers early) less common among top traders
5. **Rebalancing:** Weekly reviews, thesis-driven, not mechanical

---

## References for Analysis

- Tetlock & Gardner: *Superforecasting* (techniques)
- Rothschild (2009): Inefficiencies in Prediction Markets (bias framework)
- Zitzewitz (2004): Market Efficiency (calibration methodology)
- O'Hara (2012): Microstructure of Financial Markets (liquidity model)

**Analysis Framework Version:** 1.0
**Last Updated:** 2026-03-31
**Status:** Ready for implementation

