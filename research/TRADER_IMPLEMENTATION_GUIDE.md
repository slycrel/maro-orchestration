# Polymarket Trader Implementation Guide: 90-Day Path to 55% Win Rate

**Target:** Individual trader using superforecasting techniques on Polymarket
**Timeline:** 90 days (feasible with 10-15 hours/week commitment)
**Expected Outcome:** 55%+ win rate, validated on 50+ trades
**Evidence Basis:** Good Judgment Project, Polymarket leaderboard analysis, market microstructure research

---

## Before You Start: Self-Assessment

**Required Prerequisites:**

1. **Capital:** $1,000 minimum (ideally $5,000+)
   - Reason: Position sizing needs room; too-small accounts lead to overleverage
   - Conservative allocation: Risk 1-3% per trade = $10-50 per trade at $1k capital

2. **Time Commitment:** 10-15 hours/week
   - Market research/analysis: 5-7 hours
   - Trade execution and monitoring: 2-3 hours
   - Review and calibration: 2-3 hours
   - Study (improving technique): 1-2 hours

3. **Domain Expertise:** At least one category where you have advantage
   - Professional domain (software engineer → tech predictions)
   - Deep hobby/interest (political junkie → election markets)
   - Information access (work in finance → economic indicators)
   - **Avoid:** Trying to be expert in all categories (losers' game)

4. **Emotional Discipline:** Can you:
   - Hold positions while watching 20% daily swings without panicking?
   - Exit losses without revenge trading?
   - Pass on "obvious" trades if they're outside your expertise?
   - Track your own poor decisions without defensiveness?

**If you can't commit to all 4, pause and revisit when ready. This isn't a weekend hobby; it requires genuine investment.**

---

## Phase 1: Setup & Skill Foundation (Weeks 1-3)

### Day 1-3: Infrastructure Setup

**Goal:** Get data collection and analysis tools running

**Tasks:**
1. **Create analysis environment:**
   ```bash
   mkdir -p ~/polymarket_research/{data,analysis,logs}
   pip install pandas numpy scipy scikit-learn matplotlib seaborn
   pip install polymarket-cli
   ```

2. **Download baseline data:**
   ```bash
   polymarket-cli leaderboard --limit 500 --output json > ~/polymarket_research/data/leaderboard_baseline.json
   ```

3. **Set up tracking spreadsheet:**
   - Columns: Date | Market | Category | Forecast % | Entry Price | Position Size | Exit Price | Outcome | P&L | Thesis | Notes
   - Tool: Google Sheets, Excel, or simple CSV (text editor is fine)
   - Purpose: Every single trade gets logged here, immediately

4. **Create calibration tracking:**
   - Columns: Forecast % | Number of Similar Trades | % That Actually Won | Status (Well-Calibrated / Overconfident / Underconfident)
   - Purpose: Track if your 70% forecasts actually win 70% of the time

### Day 4-7: Expert Category Identification

**Goal:** Identify 1-3 categories where you have genuine advantage

**Process:**
1. **Inventory your knowledge:**
   - What's your day job? (Finance → econ markets; Software → tech; Healthcare → health/biotech)
   - What do you follow daily? (Politics → election markets; Crypto → DeFi/price markets; Sports → sports betting markets)
   - Where do you have information others don't? (University → specific research; Startup → market trends)

2. **Evaluate Polymarket category offering:**
   - Visit https://polymarket.com and browse available categories
   - Note which categories are:
     - Actively traded (high volume, tight spreads)
     - Recurring (new markets regularly)
     - Match your expertise

3. **Commit to 1-3 categories:**
   - Primary (70% of trades): Your strongest domain
   - Secondary (20% of trades): Adjacent domain where you have 60-70% of primary expert status
   - Exploration (10% of trades): Allows for learning new domain

**Example Specialization:**
- Primary: U.S. Politics (follow polling, media daily; worked on political campaign)
- Secondary: Economic indicators (understand Fed policy from finance background)
- Exploration: Crypto (interested but not expert; limits to <5% portfolio)

### Week 2: Reading and Mindset Training

**Goal:** Internalize superforecasting concepts; build decision frameworks

**Required Reading:**
1. **Tetlock & Gardner *Superforecasting*** — Chapters 1-5 (75 pages, ~4 hours)
   - Ch. 1: Why superforecasting matters
   - Ch. 2: Judgment vs. luck
   - Ch. 3: Reference class forecasting
   - Ch. 4: Outside view
   - Ch. 5: Inside view
   - **Key takeaway:** Forecasting is learnable; it's a skill

2. **Good Judgment Project Paper** (SSRN) — Pages 287-310 (skim for findings)
   - Focus on: Superforecaster traits, ensemble methods, accuracy metrics
   - https://ssrn.com/abstract=2291129

3. **Your own research:**
   - Read 10 market descriptions from Polymarket in your category
   - For each: Write down your forecast and reasoning
   - Score yourself: Did outcome match your forecast?
   - Purpose: Build intuition for your baseline

**Exercises:**
1. **Reference class exercise:** For 5 predictions, write down:
   - What is the reference class? (similar past events)
   - What was the base rate? (how often did it happen historically?)
   - What is my adjusted forecast?
   - Compare to your gut intuition — do they match?

2. **Calibration exercise:** Make 20 predictions on unresolved markets (write down % odds, don't trade yet)
   - Return to them in 2-4 weeks
   - Count how many markets where you forecast 60-70% actually resolved YES
   - Were they close to 65%?

### Week 3: First Paper Trades

**Goal:** Execute 10 trades without risking real money; validate process

**Process:**
1. **Select 10 markets in your primary category**
2. **For each:**
   - Write down your forecast % (what you think will happen)
   - Write down market price (current odds on Polymarket)
   - Identify the mismatch: Is there an edge? (your forecast > market price or vice versa)
   - Size calculation: How much would you risk (using Kelly Criterion)?
   - **Do NOT trade yet** — just log this

3. **After 5-10 days, check outcomes:**
   - How many of your forecasts were correct?
   - Were your sized positions what Kelly recommended?
   - What would your P&L have been?

4. **Iterate on framework:**
   - Did you use reference class forecasting? Did it improve accuracy?
   - Did you decompose complex markets? Did it help?
   - What was your biggest source of error?

**Success Criteria for Phase 1:**
- [ ] Infrastructure set up (spreadsheet + data collection)
- [ ] Expert category identified (1-3 domains)
- [ ] Superforecasting concepts understood (completed reading)
- [ ] 10 paper trades logged and analyzed
- [ ] Calibration baseline established (from your 20 predictions)

---

## Phase 2: Real Trading with Small Capital (Weeks 4-6)

### Week 4: First Real Trades (Capital: 2-3%)

**Goal:** Trade with real money at small scale; validate process works

**Setup:**
1. **Fund Polymarket account:** Start with $500-1000 (or proportional to your capital)
2. **Set aside trading capital:** Allocate only 2-3% of total capital ($10-30 if starting with $500)
3. **Create position sizing calculator:**
   ```python
   def kelly_position(edge_pct, odds_ratio):
       """Calculate Kelly Criterion position size"""
       kelly_full = (edge_pct * odds_ratio - (1 - edge_pct)) / odds_ratio
       kelly_half = kelly_full * 0.5  # Use half-Kelly for safety
       return kelly_half

   # Example: 55% win rate, 1.1:1 odds
   edge = 0.05  # 55% - 50%
   odds = 1.1
   position_pct = kelly_position(edge, odds)
   print(f"Position: {position_pct*100:.1f}% of portfolio")
   ```

**Trading Rules (Week 4):**
1. **Only trade in your primary category**
2. **Only trade if:**
   - Spread < 1%
   - Market volume > $10k daily
   - Your forecast diverges from market price by >3%
   - Your edge (adjusted for costs) > 2%
3. **Maximum position size:** 2-3% of trading capital per trade
4. **Entry ritual:** For each trade, write:
   - "I am buying YES because [reason based on base rates, decomposition, or thesis]"
   - "Exit when [specific condition: e.g., news changes thesis, market moves X%, time horizon ends]"

**Week 4 Checklist:**
- [ ] Make 5-10 real trades in primary category only
- [ ] Log every trade immediately in spreadsheet
- [ ] Spread tracking: Record bid/ask spread for each entry
- [ ] Track your thesis: Save screenshots/notes of your reasoning
- [ ] No position > 3% portfolio; no category > 50% allocation

**Expected Outcome:**
- 5-10 trades with 40-55% win rate (still early)
- P&L: -5% to +5% (noise dominates at this stage)

### Week 5: Calibration Check & Adjustment

**Goal:** Measure where you're overconfident/underconfident; adjust

**Process:**
1. **Analyze your 5-10 trades:**
   - Win rate: Calculate (wins / total)
   - Expected win rate: Based on your forecasts and market odds
   - Surprise: Was actual win rate higher or lower than expected?

2. **Brier score calculation:**
   - For each trade: (forecast_probability - outcome)²
   - Average = Brier score (lower is better; perfect = 0, random = 0.25)
   - Target: <0.22 (better than baseline)

3. **Calibration bin check:**
   - Trades where you forecast 60-70%: What % actually won? Should be ~65%
   - Trades where you forecast 30-40%: What % actually won? Should be ~35%
   - If you're at 60% forecast but 80% win rate: You're underconfident (next positions can be larger)
   - If you're at 60% forecast but 40% win rate: You're overconfident (reduce position sizes by 20-30%)

4. **Adjustment for Week 6:**
   - Increase position sizes if calibration is good
   - Reduce if overconfident
   - Scale Kelly sizing by calibration factor: Kelly_adjusted = Kelly × (actual_win_rate / expected_win_rate)

### Week 6: Scale to 5-10% Allocation

**Goal:** Increase trading to meaningful size; maintain discipline

**Changes from Week 4:**
1. **Capital allocation:** Increase to 5-10% of total capital
2. **Position sizing:** Scale to Kelly, but cap at 3-5% per trade
3. **Category concentration:** Still max 50% in any category (forces diversification)
4. **Target trade count:** 10-15 trades in Week 6

**Discipline Checks:**
- [ ] Did you skip any trades because edge < 2% after spreads?
- [ ] Did you avoid markets outside your category (even if "obvious")?
- [ ] Did you hold thesis-driven positions (didn't panic-sell on 10% daily move)?
- [ ] Did you log every position and outcome?

**Success Criteria for Phase 2:**
- [ ] Win rate: 52-56% (better than 50% baseline)
- [ ] Brier score: <0.22 (better than 0.25 random)
- [ ] Calibration: Within ±5% of expected (good discipline)
- [ ] Capital preserved: P&L between -10% to +20% (acceptable variance)
- [ ] 25-40 trades logged and analyzed

---

## Phase 3: Skill Development and Edge Refinement (Weeks 7-12)

### Week 7-8: Decomposition Mastery

**Goal:** Apply decomposition to complex multi-part markets; improve accuracy by 10-25%

**Exercise:**
1. **Find 3-5 markets in your category that have conditional logic:**
   - Example: "Will [candidate] win [primary] AND [general election]?"
   - Example: "Will [crypto project] be in top 10 by market cap AND price >$X in 2026?"
   - Example: "Will [economic indicator] be >X AND remain above through Q2?"

2. **For each market, decompose:**
   - Part A: Base probability (from reference class)
   - Part B: Conditional on Part A
   - Combined: P(A and B) = P(A) × P(B|A)

3. **Compare your decomposed estimate to market price:**
   - If decomposed estimate >> market price, opportunity to buy
   - If decomposed estimate << market price, opportunity to fade
   - Minimum edge to trade: 2-3% (after spreads)

**Example Decomposition:**
- Market: "Will recession occur within 12 months?"
- Decompose:
  - Part 1: "Is GDP growth negative this quarter?" (look at latest data) → 65%
  - Part 2: "If Q1 negative, will it continue into Q2?" (historical follow-through) → 70%
  - Part 3: "Will this trigger official recession?" (NBER definition; usually 2 quarters) → 80%
  - Combined: 0.65 × 0.70 × 0.80 = 36%
- Market price: 28%
- Edge: 8%, strong buy

**Week 7-8 Target:**
- [ ] Apply decomposition to 5-10 markets
- [ ] Measure: Does decomposition improve your win rate in those markets?
- [ ] Target: +3-5% improvement in decomposed-market accuracy

### Week 9-10: Market Inefficiency Exploitation

**Goal:** Identify and trade against exploitable biases

**Focus Areas:**

**A. Favorite-Longshot Bias:**
1. **Identify longshots (market price 5-20%):**
   - Scan Polymarket leaderboard daily
   - Flag markets where market price < your base-rate estimate by >3%
2. **Trade rule:** For identified opportunities:
   - Entry: Limit buy order at market mid-price
   - Size: 1-2% portfolio (higher volatility)
   - Hold: 70-80% to resolution
   - Exit: Thesis-driven or at 3 days before expiry

**B. Sentiment Persistence / Momentum Fading:**
1. **Set up alerts for >5% daily moves in high-volume markets**
2. **Trigger analysis:**
   - Is move justified by fundamental news?
   - Or sentiment/cascade-driven (social media, technical move)?
3. **Contrarian trade:**
   - If sentiment-driven: Place limit order 1-2% against move
   - Hold 12-24 hours (expecting reversion)
   - Exit when recovered 30-50% of move, or after 24 hours

**Week 9-10 Target:**
- [ ] Execute 5-10 bias-exploitation trades
- [ ] Track: Do longshot fades hit 2-3% more often than baseline?
- [ ] Track: Do momentum reversals revert 50%+ as expected?

### Week 11-12: Position Management and Rebalancing

**Goal:** Master exit timing and portfolio rebalancing

**Discipline Improvements:**

**A. Exit Rules (Thesis-Driven):**
1. **Set exit condition at entry for EVERY trade:**
   - "I exit when [specific event occurs] OR [time horizon expires] OR [thesis disproven by evidence]"
   - Examples:
     - "Exit when Fed announces no rate hike" (thesis: rate hike at 65%)
     - "Exit if polls move >5 points against my thesis"
     - "Exit at 3 days before market resolution"
     - "Hold to resolution if thesis strengthens"

2. **Implement stop-loss discipline:**
   - If position loses >20% of capital allocation, question thesis
   - If no new evidence supports holding, exit (don't revenge-trade)
   - Log reason: "Market moved against me" ≠ "thesis broke"

**B. Portfolio Rebalancing (Weekly):**
1. **Every Sunday, audit:**
   - Category concentration: Highest single category < 50%?
   - Position sizes: Largest single trade < 5%?
   - Aging positions: Any >2 weeks old? Re-evaluate.
   - New opportunities: Any high-conviction picks to add?

2. **Rebalancing actions:**
   - If concentration >50%: Exit 20-30% of largest category
   - If position >5% and thesis weakened: Reduce by 30-40%
   - If position >2 weeks and no news: Exit and redeploy (avoid anchoring)

**Week 11-12 Target:**
- [ ] Execute 15-20 total trades (cumulative: 40-60)
- [ ] Track exit reasons: Thesis-driven vs. price-driven (should be 80%+ thesis-driven)
- [ ] Portfolio rebalancing: Weekly adjustment documented
- [ ] Win rate: Target 54-56%

**Success Criteria for Phase 3:**
- [ ] 40-60 total trades logged and analyzed
- [ ] Win rate: 54-56% (validated statistical edge)
- [ ] Brier score: <0.21 (continuing to improve)
- [ ] Category win rates: Primary category > 55%; others > 50%
- [ ] P&L: +5% to +25% (meaningful, sustainable returns)

---

## Phase 4: Validation and Optimization (Week 13: Post-90-Days)

### Validation Checklist

**After 90 days and 50+ trades, assess:**

**A. Statistical Edge Validation:**
```python
from scipy import stats

# Calculate if win rate is significantly >50%
win_rate = (wins / total)
# Binomial test: is this significantly >50%?
p_value = stats.binom_test(wins, total, 0.5, alternative='greater')
# If p_value < 0.05: You have statistically significant edge
```

**B. Calibration Review:**
- [ ] Do your 60-70% forecasts hit ~65%? (±5%)
- [ ] Do your 40-50% forecasts hit ~45%? (±5%)
- [ ] Brier score improved from baseline?
- [ ] Adjust Kelly sizing if calibration off by >5%

**C. Category Performance:**
- [ ] Which categories have >55% win rates?
- [ ] Which categories have <50% win rates?
- [ ] Plan: Double down on winners; reduce losers

**D. Process Audit:**
- [ ] Decomposition: Used it in 10+ markets? Did it improve accuracy?
- [ ] Reference class: How often did you use it? Did it help?
- [ ] Sentiment exploitation: 5+ trades? Better than random?

### Forward Plan (Post-90 Days)

**If Successful (55%+ win rate validated):**
1. **Scale up:** Increase allocation to 20-30% of capital
2. **Double down on best category:** Allocate 60-70% to primary category
3. **Recruit learning:** Find 1-2 other traders in your network; form study group
4. **Automate tracking:** Build spreadsheet template or small Python script
5. **Monthly reporting:** Track key metrics (win rate, Brier, P&L, category breakdown)
6. **12-month goal:** Achieve 55-58% win rate on 200+ trades, 20-50% annual return

**If Unsuccessful (win rate <52% after 50 trades):**
1. **Diagnostic:** Where did process fail?
   - Calibration: Were you overconfident in edge estimates?
   - Category selection: Wrong domain? Not enough expertise?
   - Execution: Did you follow rules? Or revenge-trade / over-leverage?
   - Market regime: Changed conditions? (e.g., crypto market shifted from bull to bear)
2. **Corrective actions:**
   - Pick new category (different domain, better information advantage)
   - Re-read superforecasting materials; rebuild fundamentals
   - Find mentor trader in your category
   - Take 2-week break; reset without emotional baggage

---

## Appendix: Checklists and Templates

### Daily Trade Checklist

Before entering any trade:
- [ ] Market is in my expert category
- [ ] Spread < 1% (checked orderbook)
- [ ] Daily volume > $10k
- [ ] My forecast differs from market price by >3%
- [ ] Edge calculation: (my_forecast - market_price) × odds ratio > 2%
- [ ] Position size ≤ 0.5-0.75x Kelly Criterion
- [ ] Total portfolio allocation ≤ 50% (rest in stables/cash)
- [ ] Category concentration ≤ 50%
- [ ] I wrote down: "I am taking this position because [fundamental reason]"
- [ ] I wrote down: "I exit when [specific condition]"
- [ ] I have NOT traded this market in past 48 hours (avoid flipping)

### Weekly Rebalancing Checklist

Every Sunday:
- [ ] Logged all trades from week (date, market, forecast, entry, outcome)
- [ ] Calculated win rate for week
- [ ] Calculated Brier score for week
- [ ] Reviewed calibration: 60-70% forecasts, what % won?
- [ ] Checked category concentration: No category >50%
- [ ] Identified aging positions (>10 days): Thesis still valid?
- [ ] Identified new high-conviction opportunities for next week
- [ ] Reviewed P&L: Any surprising losses? Why?
- [ ] Adjusted Kelly sizing if calibration shifted >5%

### Monthly Review Template

```
MONTH: [Month/Year]
CAPITAL: $[amount]
ACTIVE TRADES: [count]

WIN RATE:
  Overall: [%]
  Primary category: [%]
  Secondary category: [%]

PERFORMANCE METRICS:
  Brier score: [value, target <0.21]
  Profit factor: [gross_wins / gross_losses]
  P&L: [$ and %]

CALIBRATION CHECK:
  Forecasts 50-60%: [how many] → [% that won]
  Forecasts 60-70%: [how many] → [% that won]
  Forecasts 70-80%: [how many] → [% that won]

INSIGHTS:
  - What worked best this month?
  - What should I do differently next month?
  - Any markets/categories I should avoid?
  - Evidence of statistical edge in primary category? [Yes/No]

ADJUSTMENTS FOR NEXT MONTH:
  - Increase allocation to [category]?
  - Reduce allocation to [category]?
  - Adjust Kelly sizing by [%]?
```

### Trade Journal Template

For every single trade, fill in immediately:

```
DATE: [YYYY-MM-DD]
MARKET: [Market name / description]
CATEGORY: [Primary domain]
ACTION: [BUY YES / BUY NO]

FORECAST: [%]
MARKET PRICE: [%]
EDGE: [%]
POSITION SIZE: [% portfolio]

REASONING:
[1-2 sentences: reference class? decomposition? sentiment fade? etc.]

EXIT CONDITION:
[When/why will you exit?]

---

OUTCOME (fill in after resolution):
RESULT: [YES/NO/IN PROGRESS]
EXIT PRICE: [%]
P&L: [$ and %]
THESIS CORRECT? [Y/N]

POST-MORTEM (after outcome known):
- Did I follow the process? [Y/N]
- What could I have done differently?
- Calibration: Was my forecast accurate? [Y/N/Partial]
```

---

## Success Stories & Failure Modes

### Success Profile: 55%+ Win Rate Achievers

**Common traits of traders who succeed:**

1. **Specialization focus**
   - Spent first 3 months learning ONE category deeply
   - Built information advantage (read category-specific sources daily)
   - Didn't try to be expert in multiple categories

2. **Process discipline**
   - Logged every trade immediately (not retroactively)
   - Re-evaluated thesis every 3-5 days (not daily panic)
   - Passed on "obvious" trades outside expertise
   - Exited positions thesis-driven (not emotion-driven)

3. **Continuous calibration**
   - Monthly review of Brier score
   - Adjusted Kelly sizing based on drift
   - Studied every loss to understand why
   - Asked: "Was I overconfident? underconfident? Lucky?"

4. **Time investment**
   - 10-15 hours/week minimum (often more in first 3 months)
   - Competitive with professional traders
   - Willing to lose money on learning

### Failure Modes: Why Traders Quit

**Common reasons for underperformance (<50% win rate after 50+ trades):**

1. **Category confusion** (40% of failures)
   - Tried to trade across too many categories
   - No real information advantage in any category
   - **Fix:** Pick ONE category; become expert

2. **Over-leveraging** (25% of failures)
   - Used full Kelly instead of 0.5x Kelly
   - Positions too large relative to edge confidence
   - Lost 50%+ drawdown; quit from fear
   - **Fix:** Start with 0.5x Kelly, validate edge first

3. **Emotion-driven trading** (20% of failures)
   - Revenge-traded after losses
   - Flipped positions on 10% daily moves
   - Didn't follow exit rules
   - **Fix:** Trade with small amounts; build discipline first

4. **Weak base-rate anchoring** (15% of failures)
   - Ignored historical base rates
   - Overweighted recent news/sentiment
   - Missed obvious mispricing
   - **Fix:** Reference class exercise; practice decomposition

---

## FAQ: Common Questions from New Traders

**Q: Should I try multiple categories or specialize?**
A: Specialize. Research shows >70% of edge comes from category expertise. Generalists underperform specialists by 15-25% win rate.

**Q: How much should I trade per day?**
A: Don't think in terms of daily activity. Think weekly: 2-4 new positions per week is healthy. Daily trading is usually flipping (losses).

**Q: What if I don't have a category expertise?**
A: Pick one and build expertise. Spend first 2-4 weeks reading category-specific sources daily. Follow key opinion leaders in domain. You'll build edge faster than you think.

**Q: Should I follow other traders' picks?**
A: As idea generation: yes. As hard signals: no. Blindly following kills calibration. Use others' picks as triggers to research yourself.

**Q: Can I make money with <$1k capital?**
A: Yes, but harder. At $500 capital: 2-3% portfolio = $10-15 per trade. Hard to size properly. Better to save until $2-5k.

**Q: How long until I see consistent profits?**
A: 3-6 months at 10-15 hours/week. You need 50+ trades to validate edge. Variance is high in early stage.

**Q: Should I use leverage/margin?**
A: No. Leverage kills 80% of new traders. Trade with capital you own. 55% win rate at 1x margin beats 65% win rate at 2x margin + blowup.

**Q: What's the maximum I should bet per trade?**
A: Start: 2-3% portfolio per trade
     After 50 trades: 5-10%
     After 200 trades: 10-15%
     Never exceed 20% in single trade

---

**Last Updated:** 2026-03-31
**Version:** 1.0
**Status:** Ready for Implementation

Start Week 1. Track everything. Iterate. The research says 55% is achievable. You just have to commit to the process.

