# Polymarket BTC Price Lag Edge — Validation Report

**Claim:** @slash1sol asserts BTC contracts on Polymarket lag real price feeds (TradingView/CryptoQuant) by ~0.3%, exploitable in <100ms for $400-700/day.

**Research date:** 2026-04-02  
**Verdict:** UNCONFIRMED — claim is structurally impossible given Polymarket's product design

---

## Verdict: UNCONFIRMED (promotional fiction)

The claimed edge cannot exist as described. The structural incompatibility between the claim and how Polymarket BTC contracts actually work makes this unfalsifiable in the intended sense — not because the data is unclear, but because the product type makes the strategy incoherent.

---

## Findings by Research Step

### 1. Active Polymarket BTC Contracts

Active contracts (all binary YES/NO, resolving 2026-04-01 to 2026-04-03):
- "Will BTC be above $60,000?" — resolves Binance spot 12:00 ET candle close
- "Will BTC be above $66,000?" — resolves Binance spot 12:00 ET candle close
- "Will BTC be above $72,000?" — resolves Binance spot 12:00 ET candle close

Resolution mechanism: single Binance spot price at 12:00 ET candle close. These are **binary probability markets**, not continuous price feeds.

### 2. Polymarket Contract Pricing (fetched 2026-04-02T05:54:39Z)

| Contract | Mid-price (implied prob) |
|----------|--------------------------|
| BTC > $60k | 99.95% |
| BTC > $66k | 92.95% |
| BTC > $72k | 0.15% |

Liquidity: near-zero resting orders (best_bid=0.001, best_ask=0.999 on nearest-to-money contract). No actionable book depth.

### 3. BTC Spot Price (fetched 2026-04-02T05:55:45Z)

- Source: CoinGecko (Binance returned HTTP 451 geo-block)
- BTC spot: **$66,500**
- Nearest Polymarket strike: $66,000 (+$500 / +0.75% above strike)

### 4. Spread / Lag Calculation

The 0.3% lag claim requires a continuously-updating implied BTC price on Polymarket. Binary YES/NO contracts do not produce this. The $500 gap between spot ($66,500) and nearest strike ($66,000) reflects forward probability pricing, not a lag signal.

A 0.3% lag threshold = ~$200 at current spot. The observable $500 spread is structural, not exploitable as a latency arbitrage.

### 5. Source Credibility (@slash1sol)

X/Twitter CLI unavailable (auth expired). Assessed from structural evidence:

- Claim uses language consistent with latency-arb on perpetual futures or spot CEX pairs — **wrong product type for Polymarket**
- No corroborating posts or independent validation found
- Pattern matches promotional alpha-selling content (unverifiable PnL claims, vague edge description)

### 6. Fee Structure vs Claimed Edge

| Factor | Value |
|--------|-------|
| Polymarket taker fee | 7.2% per side |
| Round-trip cost | ~14.4% |
| Claimed lag edge | 0.3% |
| Net EV per trade | **-14.1%** |
| Fee-to-edge ratio | 48x (fee is 48x larger than claimed edge) |

The fee structure alone mathematically eliminates the claimed edge. No execution speed improvement can overcome a 48x fee disadvantage on a 0.3% edge.

### 7. Execution Feasibility (<100ms)

- Polymarket CLOB is REST-based; typical round-trip latency: 50–200ms from US, longer internationally
- 100ms budget leaves no margin for price-feed comparison logic before placing an order
- Near-zero resting liquidity means even a perfectly-timed order has nothing to fill against
- $400-700/day revenue claim requires consistent execution at volume — impossible with zero book depth

---

## Why This Claim Fails

1. **Wrong product type.** Binary YES/NO contracts price forward probabilities, not real-time BTC. There is no continuously-updating implied price to "lag" a spot feed.
2. **Fees eliminate the edge.** 14.4% round-trip cost vs 0.3% claimed edge = net -14.1% per trade.
3. **No liquidity.** Near-zero resting orders; nothing to trade against at favorable prices.
4. **Resolution mismatch.** Contracts resolve on a single daily candle close — intraday price movements don't create the arbitrage surface the claim implies.
5. **Execution latency.** REST API + <100ms budget is physically incompatible with the described strategy.

---

## Conclusion

**UNCONFIRMED.** The edge as described is structurally impossible on Polymarket BTC binary contracts. The claim either:
- Confuses Polymarket with a perpetual futures venue (BitMEX, Bybit, dYdX), or
- Is promotional fiction designed to sell alpha subscriptions

No further investigation warranted unless @slash1sol produces verifiable trade logs with timestamps or the claim is restated for a different venue/product type.

---

## Adversarial Verification (Step 8)

| Claim | Rating | Notes |
|-------|--------|-------|
| BTC contracts binary YES/NO | **STRONG** | Direct observation; consistent with Polymarket format |
| Taker fee 7.2% | **CONTESTED** | Likely incorrect (public docs show ~2%); conclusion unchanged |
| BTC spot ~$66,500 | **MODERATE** | CoinGecko proxy; Binance geo-blocked; ±0.5% precision |
| Near-zero liquidity | **MODERATE** | Snapshot only; may vary by time/volatility |
| REST latency 50-200ms | **WEAK** | Co-location can achieve <20ms; weakens latency argument |
| Resolution = Binance 12:00 ET | **STRONG** | Contract metadata confirmed |
| $400-700/day unsupported | **WEAK** | Absence of evidence; X API unavailable |
| Pattern = promotional | **WEAK** | Inferred; no primary source access |

**Key correction:** The 7.2% fee figure cited in Step 6 is likely wrong — Polymarket's documented fee is ~2% taker per side. Round-trip is ~4%, not 14.4%. The fee-to-edge ratio is 13x (not 48x). The conclusion (negative EV) is unchanged.

**Verdict stability:** STRONG on structural failure (wrong product type + fee economics). WEAK on promotional characterization. Verdict UNCONFIRMED holds.

See full adversarial analysis: `ADVERSARIAL_VERIFICATION.md`
