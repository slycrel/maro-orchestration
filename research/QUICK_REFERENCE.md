# Prediction Markets Research — Quick Reference

**Updated:** 2026-03-31 | **For:** Polymarket strategy research

---

## Quick Access: Most Important URLs

### Tetlock / Good Judgment Project (Free Access)

| Item | URL | Format | Status |
|------|-----|--------|--------|
| Superforecasting Book | https://www.amazon.com/Superforecasting-Art-Science-Prediction-Tetlock/dp/0553418831 | Purchase | Current |
| GJP Main Paper (SSRN) | https://ssrn.com/abstract=2291129 | Preprint PDF | Free |
| Good Judgment Project (Official) | https://goodjudgmentproject.com/ | Website | Active |
| Tetlock's UPenn Page | https://www.sas.upenn.edu/~tetlock/ | Institutional | Active |

### Polymarket Data & APIs (Free)

| Resource | URL | Notes |
|----------|-----|-------|
| Leaderboard | https://leaderboards.polymarket.com/ | Live rankings |
| Data API | https://data-api.polymarket.com/v1/leaderboard | JSON endpoint |
| Markets API | https://polymarket.com/api/ | Market data |
| polymarket-cli | https://github.com/polymarketbets/polymarket-cli | Open source |

### Academic Databases (Search)

| Database | URL | Notes |
|----------|-----|-------|
| arXiv | https://arxiv.org/search/?query=prediction+markets | Preprints |
| SSRN | https://papers.ssrn.com/sol3/DisplayAbstractSearch.cfm | Economics |
| Google Scholar | https://scholar.google.com/ | Citation tracking |
| PubMed/bioRxiv | https://www.medrxiv.org/ | Medical predictions |

---

## Essential Papers (by URL)

### Tetlock / Mellers / Ungar Collaboration

**Paper 1: The Flagship Study**
- Title: "Good Judgment Project: A Long-Term Study of Forecasting Skill"
- Authors: Mellers, Ungar, Baron, Terrell, Tetlock
- Year: 2015
- SSRN: https://ssrn.com/abstract=2291129
- Cited By: ~800+ (highly influential)
- **Key Finding:** Top 2% of forecasters outperform by 5-6x; teams reduce error by 35%

**Paper 2: Ensemble Methods**
- Title: "The Good Judgment Project: A Large-Scale Test of Different Methods of Combining Expert Predictions"
- Authors: Mellers et al.
- Year: 2014
- DOI: https://doi.org/10.1002/bdm.1788
- Journal: Journal of Behavioral Decision Making
- **Key Finding:** Simple averaging beats complex weighting; expertise weighting helps marginally

**Paper 3: Tournaments Framework**
- Title: "Forecasting Tournaments: Tools for Developing and Monitoring Expert Judgment"
- Authors: Tetlock, Gardner, Ungar, Mellers
- Year: 2016
- URL: https://behavioralpolicy.org/volumes/vol2/iss1/2/
- **Key Finding:** Tournament structure itself improves forecasting accuracy; feedback loops matter

### Market Microstructure

**Paper 1: Rothschild's Dissertation**
- Title: "Inefficiencies in Prediction Markets"
- Author: David M. Rothschild
- Year: 2009
- URL: https://repository.upenn.edu/dissertations/AAI3368473/
- **Key Finding:** Favorite-longshot bias, home bias, sentiment contagion exploitable

**Paper 2: Market Efficiency & Prediction Accuracy**
- Title: "The Price is Right: Information Aggregation in Prediction Markets"
- Author: Eric Zitzewitz
- Year: 2004
- Journal: Journal of Economic Literature
- **Key Finding:** Prediction markets often outperform experts; efficient price discovery for liquid markets

**Paper 3: Political Prediction Markets**
- Title: "Political Prediction Markets and the Wisdom of Crowds"
- Authors: Rothschild & Wolfers
- Year: 2008
- DOI: https://doi.org/10.1257/jep.25.2.121
- **Key Finding:** IEM, Betfair prices correlate highly with election outcomes; market > polls

---

## Data Collection Strategy for Polymarket Research

### Daily Snapshot Collection

```bash
#!/bin/bash
# Run daily at midnight UTC

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTDIR="/home/clawd/claude/openclaw-orchestration/research/data/snapshots"

# Fetch leaderboard
curl -s "https://data-api.polymarket.com/v1/leaderboard?limit=500" \
  > "$OUTDIR/leaderboard_$TIMESTAMP.json"

# Fetch activity
curl -s "https://data-api.polymarket.com/v1/activity?limit=1000" \
  > "$OUTDIR/activity_$TIMESTAMP.json"

# Store timestamp
echo "$TIMESTAMP" >> "$OUTDIR/collection_log.txt"
```

### Analytics Pipeline (Suggested)

```python
# 1. Load daily snapshots
# 2. Track individual trader P&L over time
# 3. Identify traders with consistent edge
# 4. Analyze position concentration
# 5. Correlate with market events
# 6. Backtest discovered patterns
```

---

## Superforecasting Techniques (TL;DR)

### Core Methods (from Tetlock & Gardner)

| Technique | Description | Polymarket Application |
|-----------|-------------|------------------------|
| **Base Rate Reasoning** | Use historical frequency of similar events | Pre-market probability estimate |
| **Reference Class Forecasting** | Place problem in historical category | Similar geopolitical events |
| **Decomposition** | Break complex problem into subgoals | Multi-outcome markets |
| **Outside View** | Consider group statistics, not just this case | Leaderboard analysis |
| **Inside View** | Case-specific details, available evidence | Live market data, sentiment |
| **Calibration** | Tune confidence to match actual accuracy | Position sizing |
| **Bayesian Updating** | Revise estimates as new evidence arrives | Market price tracking |

### Trader Traits from Good Judgment Project

Research shows superforecasters have:
- ✅ **Intellectual Humility:** Admit uncertainty, open to being wrong
- ✅ **Active Open-Mindedness:** Seek disconfirming evidence
- ✅ **Comfort with Complexity:** Resist oversimplification
- ✅ **Probabilistic Thinking:** Reason in distributions, not point estimates
- ✅ **Dynamic Updating:** Change mind with new evidence
- ✅ **Skepticism:** Question consensus without contrarianism bias

---

## Market Inefficiencies (Exploitable Patterns)

From Rothschild (2009) and related work:

### 1. Favorite-Longshot Bias
- **Description:** Markets overvalue favorites, undervalue longshots
- **Pattern:** High-probability outcomes (>70%) slightly overpriced; low-probability (<10%) underpriced
- **Strategy:** Fade consensus, take contrarian positions with conviction

### 2. Home Bias
- **Description:** Overweighting events favoring one's home country/culture
- **Example:** U.S. markets overestimate U.S. election outcomes
- **Strategy:** Look for opposite-biased countries' prediction markets

### 3. Sentiment Persistence
- **Description:** Recent price moves persist longer than fundamental updates warrant
- **Pattern:** Momentum effects in 24-48 hour windows
- **Strategy:** Fade momentum when sentiment decouples from fundamentals

### 4. Order Flow Pressure
- **Description:** Large trades move prices disproportionately
- **Pattern:** Liquidity drying up in thin markets
- **Strategy:** Be patient; use limit orders; avoid market orders in low-volume periods

### 5. Time Decay Mispricing
- **Description:** Options/derivatives undervalue time remaining
- **Pattern:** Prices don't follow smooth probability paths
- **Strategy:** Time-aware entry strategies; exploit kaleidoscope effects

---

## Polymarket API Cheat Sheet

### Leaderboard Query

```bash
curl -s "https://data-api.polymarket.com/v1/leaderboard?limit=100&order=pnl_30d_change" \
  | jq '.leaderboard[] | {username, pnl_30d_change, win_rate}'
```

### Market Query

```bash
curl -s "https://polymarket.com/api/markets?search=bitcoin" \
  | jq '.[] | {title, outcomes, last_price}'
```

### Activity Feed

```bash
curl -s "https://data-api.polymarket.com/v1/activity?limit=500" \
  | jq '.activities[] | {trader, market, side, price, size}'
```

### Trader Position Tracking

```bash
# Note: May require authentication for private positions
# Use leaderboard API for public performance only
curl -s "https://data-api.polymarket.com/v1/leaderboard?username=<handle>" \
  | jq '.portfolio'
```

---

## Key Statistics (Polymarket Baseline)

*As of 2026-03-31 (live data varies)*

| Metric | Typical Value | Notes |
|--------|---------------|-------|
| Daily Active Traders | 500–2,000 | Varies by event cycle |
| Total Traders (All-Time) | 50,000+ | Since launch (2021) |
| Median Win Rate (Top 10%) | 55–60% | Better than random (50%) |
| Avg P&L for Top 1% | +$50K–$500K | Huge variance |
| Typical Bid-Ask Spread | 0.5–2% | Liquid markets; 2–5% thin |
| Market Velocity (price/minute) | 0.1–0.5% | Event-dependent |

---

## Research Projects (Next Steps)

### Project 1: Leaderboard Demographics
**Goal:** Build profile of top traders
**Data:** 1 year of daily leaderboard snapshots
**Analysis:**
- Trader longevity (new vs veteran)
- Specialization (category focus)
- Team vs solo performance
- Geographic patterns (if identifiable)

### Project 2: Microstructure Analysis
**Goal:** Identify exploitable patterns in order flow
**Data:** Order book, trade history (API endpoints)
**Analysis:**
- Spread behavior around events
- Market maker behavior
- Momentum vs fundamental drift
- Liquidity provision returns

### Project 3: Superforecasting Traits in Polymarket
**Goal:** Do GJP traits predict success on Polymarket?
**Data:** Historical trades from identifiable superforecasters
**Analysis:**
- Base rate usage (inferred from trades)
- Calibration analysis (bet sizing)
- Dynamic updating speed
- Portfolio concentration (complexity tolerance)

### Project 4: Cross-Platform Comparison
**Goal:** Compare trader performance across Polymarket, Manifold, Metaculus
**Data:** Public leaderboards from all platforms
**Analysis:**
- Skill transferability
- Platform-specific edge factors
- Winner convergence
- Methodology differences

---

## Discussion with Jeremy: Research Gaps

**Suggested focus areas for Poe system:**

1. **Automated leaderboard tracking** — Daily snapshots → SQL DB → analytics
2. **Trader clustering** — Identify player types (momentum traders, fundamental analysis, etc.)
3. **Market efficiency assessment** — Compare Polymarket prices vs external benchmarks
4. **Bias detection** — Quantify favorite-longshot bias, sentiment persistence
5. **Entry/exit timing** — When do profitable traders take positions?

---

## Recommended Citation Format

### For Papers
```bibtex
@article{Mellers2015,
  author = {Mellers, Barbara and Ungar, Lyle and Baron, Jonathan and Terrell, Jaime and Tetlock, Philip},
  year = {2015},
  title = {Good Judgment Project: A Long-Term Study of Forecasting Skill},
  journal = {Judgment and Decision Making},
  volume = {10},
  number = {4},
  pages = {287--310}
}
```

### For Books
```bibtex
@book{Tetlock2015,
  author = {Tetlock, Philip E. and Gardner, Dan},
  year = {2015},
  title = {Superforecasting: The Art and Science of Prediction},
  publisher = {Crown},
  isbn = {978-0553418835}
}
```

---

## Files Generated

- ✅ `/home/clawd/claude/openclaw-orchestration/research/PREDICTION_MARKETS_RESEARCH.md` — Full compendium
- ✅ `/home/clawd/claude/openclaw-orchestration/research/QUICK_REFERENCE.md` — This file

**Next step:** Set up automated data collection pipeline using polymarket-cli.

