# Quick Decision Framework for Polymarket Traders

**Print this. Laminate it. Reference it before every trade.**

---

## The Superforecasting Decision Tree

```
Should I take this trade?
|
├─ Is this in my expert category?
│  ├─ NO → PASS (you don't have information advantage)
│  └─ YES → Continue
│
├─ Market spread < 1%? Daily volume > $10k?
│  ├─ NO → PASS (too much friction)
│  └─ YES → Continue
│
├─ Have I applied reference class forecasting?
│  ├─ NO → RESEARCH first (don't trade blind)
│  └─ YES → Continue
│
├─ Is my forecast ≠ market price by >3%?
│  ├─ NO → PASS (not enough edge)
│  └─ YES → Continue
│
├─ Is my edge (after spreads) > 2%?
│  ├─ NO → PASS (costs exceed edge)
│  └─ YES → Continue
│
├─ Can I articulate my thesis in 1-2 sentences?
│  ├─ NO → RESEARCH more (fuzzy thinking = losses)
│  └─ YES → Continue
│
├─ Do I know EXACTLY when/why I'll exit?
│  ├─ NO → DEFINE exit rule (don't wing it)
│  └─ YES → Continue
│
└─ TRADE: Position size = 0.5-0.75x Kelly Criterion
   Record: Market | Forecast | Thesis | Exit Condition
```

---

## Quick Reference: Key Numbers

### Win Rate Targets
- **Baseline (random):** 50%
- **Achievable (superforecasting):** 55-60%
- **Realistic after 90 days:** 53-55%
- **Top 10% of traders (Polymarket):** 55-60%
- **Top 1% of traders:** 60-65%

### Position Sizing
- **Start:** 2-3% per trade
- **After 50 validated trades:** 5-10%
- **Maximum safe:** 15% per trade (unless extreme conviction + data edge)
- **Full Kelly formula:** f* = (win_rate - loss_rate) / odds_ratio
- **Use:** 0.5-0.75x Kelly (not full Kelly)

### Market Quality Thresholds
- **Spread:** <0.5% (trade aggressively) | 0.5-1% (limit orders) | >1% (pass)
- **Volume:** >$100k daily (very liquid) | $10k-$100k (good) | <$10k (risky)
- **Edge needed:** 1.5% (liquid) | 2% (medium) | 3%+ (thin)

### Holding Duration
- **Optimal:** 7-60 days (thesis-driven, not time-driven)
- **Avoid:** <3 days (emotional flipping) | >70 days (aging positions)
- **Near expiry (<3 days):** Reduce position by 50-70%

### Calibration Targets
- **Brier score:** <0.21 (vs. 0.25 random)
- **Calibration drift:** ±5% acceptable (60% forecast → 55-65% actual)
- **Review frequency:** Monthly

---

## Market Mispricing Detector

**Quick scan for exploitable opportunities:**

| Pattern | Signal | Action | Target Edge |
|---------|--------|--------|-------------|
| **Favorite-Longshot** | Market >70% | Fade YES; Buy NO | 0.5-1% |
| **Favorite-Longshot** | Market <15% | Buy YES | 1-2% |
| **Sentiment Spike** | 5%+ move, no news | Fade move; buy dip | 0.5-1% |
| **Base-Rate Miss** | Reference class ≠ market | Arbitrage spread | 2-3% |
| **Decomposition Gap** | Sub-questions < combined | Check compound pricing | 1-2% |
| **Cascade Breakout** | Price breaks range | Fade breakout | 1-1.5% |

---

## The 1-Minute Pre-Trade Checklist

**Before clicking BUY or SELL, verify:**

- [ ] **Category:** This is my expertise domain
- [ ] **Spread:** Market spread visible and <1%
- [ ] **Volume:** Daily volume >$10k
- [ ] **Thesis:** I can explain in 1 sentence why I'm right
- [ ] **Reference Class:** Base rate considered (not just my gut)
- [ ] **Edge:** My forecast - market price > 3%
- [ ] **Friction:** Edge (after 0.5% spread + fees) > 2%
- [ ] **Size:** Position = 0.5-0.75x Kelly; ≤ 5% portfolio
- [ ] **Exit:** I know WHEN and WHY I exit
- [ ] **Logging:** Spreadsheet ready to record immediately

**If ANY checkbox fails:** PASS. Move on. There are infinite markets.

---

## The Weekly Rebalancing Ritual (15 minutes)

**Every Sunday evening, do this:**

1. **Count this week's trades:**
   - Wins / Losses / Win Rate: ____%
   - P&L: $____ (__%)

2. **Check calibration:**
   - Forecasts 60-70%: ____ trades → ____% won (target: 65%)
   - Adjustment needed? YES / NO

3. **Check concentration:**
   - Largest category allocation: ____% (target: <50%)
   - Largest single position: ____% (target: <5%)
   - Rebalance? YES / NO

4. **Check aging:**
   - Positions >10 days old: [list]
   - Thesis still valid? [YES/NO for each]
   - Exit candidates: [mark any to close]

5. **Scan for next week:**
   - Any high-conviction opportunities in my category?
   - New markets to watch? Add to watchlist.

**Time spent:** 15 min. Impact: Prevents 90% of portfolio disasters.

---

## Cognitive Bias Defenses

### Your Brain Will Betray You. Here's How to Fight Back:

| Bias | What It Does | Your Defense |
|------|--------------|--------------|
| **Anchoring** | First price you see sticks | Use reference class; ignore first number |
| **Recency** | Recent trend = future | Check base rates; use historical average |
| **Overconfidence** | You're better than you are | Use 0.5x Kelly; track Brier score monthly |
| **Confirmation** | You see evidence supporting thesis | Actively seek disconfirming evidence |
| **Sunk cost** | "I've already lost X..." | Exit losses if thesis breaks; don't revenge-trade |
| **Herding** | Everyone else is buying | Check against base rate; fade consensus |
| **Availability** | Dramatic examples overweighted | Use reference class, not memorable cases |

**Antidote:** Log your reasoning. Track outcomes. Compare forecast to result. This feedback loop kills biases.

---

## The Three Exit Rules

**You exit a position when:**

### Rule 1: Thesis Changes (Highest Priority)
- New evidence contradicts your original assumption
- Example: You bought YES on "Recession 12mo" at 65%. New jobs report shows 250k jobs added (strong baseline). Thesis broken → Exit.
- Decision speed: Immediate (within hours)
- Emotional state: Cold (data-driven)

### Rule 2: Time Horizon Expires
- You have a market-specific time to evaluate
- Example: You're betting on "Fed raises rates at March meeting." Meeting is next week. You hold through it. Post-meeting → Exit (win or lose).
- Decision speed: Scheduled (calendar trigger)
- Emotional state: N/A (rule-based)

### Rule 3: Position Aging (Weak Conviction)
- Position is >10 days old AND conviction has weakened
- You no longer think "I'm definitely right, just waiting for market to catch up"
- Decision speed: Weekly review (Sunday rebalance)
- Emotional state: Objective (portfolio rebalance)

**Do NOT exit because:**
- ❌ Price moved 10% against you (unless thesis broke)
- ❌ You "need" the money (reduce size, don't liquidate)
- ❌ You're bored (hold the thesis)
- ❌ Other traders are selling (fade groupthink)
- ❌ You're up 15% (let winners run)

---

## Polymarket API Cheat Sheet (for monitoring)

```bash
# Check leaderboard (top traders today)
curl -s "https://data-api.polymarket.com/v1/leaderboard?limit=50" \
  | jq '.[] | {username, pnl_30d_change, win_rate}'

# Monitor a specific market's recent trades
curl -s "https://data-api.polymarket.com/v1/activity?limit=100" \
  | jq '.[] | {market, price, timestamp}'

# Get your portfolio (if you expose API)
# Note: Requires authentication; check Polymarket docs
```

---

## Kelly Criterion Quick Calculator

**Position size = 0.5 × [(2 × win_rate) - 1] × 100%**

| Win Rate | Implied Edge | Full Kelly | Half Kelly (USE THIS) |
|----------|-----------|-----------|----------------------|
| 50% | 0% | 0% | 0% (no edge) |
| 52% | 2% | 2% | 1% |
| 53% | 3% | 3% | 1.5% |
| 54% | 4% | 4% | 2% |
| 55% | 5% | 5% | 2.5% |
| 56% | 6% | 6% | 3% |
| 57% | 7% | 7% | 3.5% |
| 58% | 8% | 8% | 4% |
| 60% | 10% | 10% | 5% |

**Example:**
- Your historical win rate in category: 55%
- New market in that category
- Half-Kelly position: 2.5% of portfolio
- Account size: $10,000
- Position size: $250

---

## Reference Class Examples (For Practice)

### Example 1: U.S. Presidential Election
**Market:** "Will Candidate X win 2028 election?"

1. **Reference class:** U.S. presidential elections 1992-2024 (9 elections)
2. **Base rate:** Incumbent party wins re-election: 5/9 = 56%
3. **Inside view:** Current polling, economic indicators, approval ratings
4. **Adjustment:** If strong economy (3%+ growth): bump to 62%
5. **Final forecast:** 60%

### Example 2: Crypto Market Price
**Market:** "Will Bitcoin be >$100k by Dec 2026?"

1. **Reference class:** Bitcoin ATH (all-time high) cycles; time to reach new ATH
2. **Base rate:** Bitcoin has reached new ATH in 4 of 5 bull cycles (~80%)
3. **Time adjustment:** We're 3 years from last ATH (2021). Typical cycle: 4-5 years.
4. **Current price:** $60k. Distance to $100k: 67% gain.
5. **Inside view:** Market conditions, halving cycles, adoption trends
6. **Final forecast:** 45-50%

### Example 3: Economic Recession
**Market:** "Will U.S. recession occur within 12 months?"

1. **Reference class:** U.S. recessions 1960-2024 (13 recessions in 64 years)
2. **Base rate:** ~20% annual probability
3. **Current indicators:** Yield curve shape, unemployment, PMI, leading indicators
4. **Adjustment:** If yield curve inverted (historical recession signal) and inverted >6 months: bump to 30%
5. **Final forecast:** 22-25%

---

## The Three Questions Before Emotional Decisions

**Every time you feel urge to panic-sell, panic-buy, or revenge-trade, ask:**

1. **Has my thesis changed?**
   - YES → Exit (thesis-driven)
   - NO → Continue to Q2

2. **Is this emotional or data-driven?**
   - Emotional → Wait 24 hours; don't trade tired/angry/scared
   - Data-driven → Continue to Q3

3. **Would I enter this position TODAY at this price?**
   - NO → Exit (position has become unfavorable)
   - YES → Hold (thesis unchanged, price is OK)

**If all three say "hold," hold.**

---

## Common Market Types in Polymarket (By Difficulty)

| Market Type | Difficulty | Edge Potential | Your Action |
|-------------|-----------|---|---|
| **Price prediction** (Crypto, stocks) | High | 1-3% | IF expert in asset class |
| **Election markets** | Medium | 2-4% | IF follow politics closely |
| **Economic indicators** | Medium | 1-3% | IF background in finance/econ |
| **Sports outcomes** | Low-Medium | 2-5% | IF follow sport closely |
| **Tech/startup milestones** | Medium | 2-4% | IF work in tech |
| **Weather/climate** | Low | 0.5-1% | AVOID (high noise) |
| **Crypto regulation** | High | 3-5% | IF follow policy closely |
| **M&A / Corporate events** | Medium | 2-3% | IF follow sector |

**Strategy:** Stick to market types where you know you have an edge. Skip the rest, no matter how "obvious" they seem.

---

## P&L Attribution: Why Did You Win/Lose?

**After every trade closes, categorize:**

| Attribution | Description | Example | Action |
|-------------|---|---|---|
| **Good skill** | Thesis correct + executed well | Recognized sentiment oversold; faded cascade | Replicate |
| **Lucky** | Thesis wrong but won anyway | Guessed on Fed decision; got lucky | Learn from close calls |
| **Unlucky** | Thesis correct but lost anyway | 70% edge, statistical outlier, lost | Track frequency |
| **Execution error** | Bad entry/exit timing | Entered too late; exited too early | Improve discipline |
| **Overconfidence** | Thesis weaker than you thought | Didn't research enough before trading | Slow down, research more |

**Track these ratios:**
- Good skill wins: 50-60% (excellent)
- Lucky wins: 5-10% (acceptable)
- Unlucky losses: 5-10% (acceptable)
- Execution errors: <10% (room for improvement)
- Overconfidence losses: <5% (watch this closely)

**If overconfidence >10%:** Reduce position sizes immediately.

---

## Emergency: Market Crashed 20% Overnight (What Do You Do?)

**Step 1:** Do NOT panic-sell. Breathe.

**Step 2:** Evaluate your positions:
- **Thesis-based positions:** Your thesis didn't change. Market moved. Hold.
- **Position-size too large (>10% portfolio):** Reduce by 30-40% (lock in some liquidity).
- **Markets in your category:** Check if you have new information. If not, hold or add (if edge still valid).

**Step 3:** Look for opportunities:
- Crashes create extreme moves and mispricings
- Sentiment-driven longshots become even more underpriced
- Fade the panic; buy dips if thesis supports

**Step 4:** Rebalance:
- Crashed market might now have poor liquidity (spreads widen)
- Positions that were 5% now maybe 20% due to crash
- Rebalance to target allocations

**Do NOT:**
- ❌ Close everything to "preserve capital" (that's selling the bottom)
- ❌ Revenge-trade (emotions highest during crashes)
- ❌ All-in on single contrarian bet (even if good edge)

---

## The Monthly Metacognitive Review (30 minutes, end of month)

**Read this after your monthly review is complete:**

1. **What surprised me this month?**
   - What market moved more/less than I expected?
   - What bias did I notice in my own forecasting?
   - Did market efficiency match my expectations?

2. **Where was I overconfident?**
   - Which positions lost money despite high conviction?
   - Did I size too large relative to my edge?
   - Did I underestimate volatility?

3. **Where was I underconfident?**
   - Which markets did I avoid that would have been profitable?
   - Did I miss obvious mispricings?
   - Did I exit too early?

4. **How is my process changing?**
   - Am I using reference class more?
   - Is my decomposition getting better?
   - Are my exit rules holding up?

5. **What's my edge next month?**
   - What's changing in my expert category?
   - Are my information sources still relevant?
   - Do I need new data sources?

**Output:** Write 3-5 bullets summarizing lessons. Email to yourself. Return to it at end of year.

---

## Status Check: Are You On Track for 55% Win Rate?

**At 25 trades:**
- Win rate should be 50-54% (high variance OK; still early)
- Brier score should be <0.23 (better than baseline)
- Process: Have you logged every trade?

**At 50 trades:**
- Win rate should be 52-55% (statistically significant >50%?)
- Brier score should be <0.22
- Calibration: Are your 60% forecasts hitting 55-65%?

**At 100 trades:**
- Win rate should be 54-57% (validated edge)
- Brier score should be <0.21
- Category dominance: Primary category >55% win rate?

**At 200 trades:**
- Win rate should be 55-58% (sustainable)
- Portfolio: Are you in top 20-30% of Polymarket traders?
- Next phase: Consider syndicate / team / larger capital?

---

## When to Walk Away (Permanently)

**Stop trading Polymarket if:**

1. **After 100+ trades, win rate is still <50%:**
   - Your method isn't working; pick different domain or exit entirely

2. **You've lost >25% of starting capital:**
   - Pause, diagnose (overconfidence? wrong category? wrong method?)
   - Take 2-week break; rebuild confidence on paper trading

3. **You're trading emotionally:**
   - Revenge-trading after losses, FOMO-buying on moves, etc.
   - Take 1-month break; return when emotionally reset

4. **Your life situation changed:**
   - Lost your job (capital not stable)
   - Major life event (can't focus)
   - New financial obligations (can't afford losses)
   - Pause until situation stabilizes

5. **Markets fundamentally changed:**
   - Your information sources are no longer relevant
   - Market regime shifted (bull to bear in crypto)
   - New regulations changed structure
   - Reassess if your edge still exists

**It's not failure. It's smart risk management.**

---

## Final Thought

**You are competing against:**
- Superforecasters who read all the same research you're reading
- Professional traders with bigger capital and faster execution
- Market makers who profit from your spread slippage
- Your own biases and emotions

**Your advantages (if you execute them):**
- You are willing to specialize (most traders try everything)
- You are willing to use systematic process (most trade gut-feel)
- You are willing to track your calibration (most ignore feedback)
- You are willing to take years to build (most want quick riches)

**55% is achievable. It's not luck. It's practice, discipline, and humility.**

Now go log your trades.

---

**Version:** 1.0 | **Last Updated:** 2026-03-31 | **Status:** Print and use
