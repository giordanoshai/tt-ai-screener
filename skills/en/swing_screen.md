# Swing Trade Screening

Holding period: 2–20 days, technically driven. From a candidate pool, identify high-probability swing entries based on momentum, volume-price action, chart patterns, and news sentiment.

## Input Data

The system automatically feeds a JSON payload — you do NOT need to query any database. Structure:

```
{
  "date": "trading day",
  "source": "built-in criteria (...)" or "screener (N tickers)",
  "candidates_count": N,
  "candidates": [
    {
      "ticker", "company_name", "sector",
      "close", "ma_20", "ma_50", "ma_200",
      "rsi_14", "vol_ratio", "atr_pct", "pct_chg",
      "dist_ma20_pct", "dist_ma50_pct", "high_20",
      "next_earnings_date", "pe_ratio", "revenue_growth_yoy",
      "recent_news": [ {"headline","sentiment_label","sentiment_score","published_at"} ]  // last 7 days, <=3
    }
  ]
}
```

Field definitions:
- `dist_ma20_pct` = (close-MA20)/MA20, positive = above the MA
- `vol_ratio` = today's volume / 20-day avg volume, >1.5 = significant surge
- `atr_pct` = ATR14/close, typical daily move, used for stop/target
- `high_20` = 20-day high, close >= high_20×0.99 counts as a breakout
- Fundamentals (pe / revenue growth) and sentiment may be null — treat missing as neutral

**Data-source difference (important):**
- `source = built-in`: the pool is already pre-filtered (close>MA20 and >MA50, MA20>MA50, vol_ratio>1.2, RSI 45-75, close near MA20). Do NOT re-run a trend screen — go straight to pattern classification.
- `source = screener`: candidates are NOT trend-filtered. First drop any that fail "close>MA50 and ma_50>ma_200", then classify.

---

## Workflow

### Step 1: Pattern Classification

Assign each candidate to exactly one pattern (mutually exclusive, first match by priority):

**Pattern A — Volume breakout to new high**
- close >= high_20 × 0.99
- vol_ratio >= 1.5

**Pattern B — Strong shallow pullback (holding MA20)**
- dist_ma20_pct ∈ [-0.02, 0.025] (negative side only appears in screener source; built-in source = 0–0.025 shallow pullback)
- dist_ma50_pct > 0.03 (medium-term trend intact)
- rsi_14 ∈ [45, 60]

**Pattern C — In-trend consolidation**
- Not A/B, but close > ma_20 > ma_50 and rsi 50-70
- Treat as "watch only", score capped at B

No pattern → discard.

---

### Step 2: News Sentiment

Using `recent_news` (last 7 days):
- avg < -0.3, or any single item with score < -0.3 (strong negative) → exclude
- avg > 0.2 → bonus
- No news (common for small/mid-caps) → rely on technicals, no penalty

---

### Step 3: Earnings & Valuation Risk

- `next_earnings_date` within 7 days → flag "earnings risk", cap score at B (avoid holding through earnings naked)
- `pe_ratio > 50 AND revenue_growth_yoy < 0.1` → flag "high-valuation low-growth", deduct
- `revenue_growth_yoy > 0.2` → bonus

---

### Step 4: Quantitative Score (0–100)

| Dimension | Max | Criteria |
|-----------|-----|----------|
| Pattern strength | 30 | A=30; B=24; C=15 |
| Volume | 25 | vol_ratio>=2 →25; 1.5-2 →20; 1.2-1.5 →12; <1.2 →5 |
| Momentum (RSI) | 20 | RSI 55-68 →20; 50-55 or 68-72 →14; 45-50 →8; >75 overbought →0 |
| Sentiment | 15 | avg>0.2 →15; -0.2~0.2 or no data →8; <-0.2 →0 |
| Risk deduction | 10 | no earnings/valuation risk →10; earnings within 7d →3; high-val low-growth →0 |

Rating: **A >= 78** (strong focus) / **B 60–77** (worth tracking) / **C < 60** (watchlist).
Earnings risk or overbought (RSI>75) → hard-capped at B and marked "wait for pullback".

---

### Step 5: Output Results

Each candidate (sorted by score desc):

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【A Breakout / B Pullback / C Consolidation】TICKER — Company Name   Rating: A/B/C (xx pts)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Price: $xxx   MA20 dist: +x.x%   MA50 dist: +x.x%
RSI: xx       Volume ratio: x.xx  ATR daily range: x.x%

Pattern: [One-line description of the current setup]
Sentiment: [Positive / Neutral / Negative / No data], 7-day avg x.xx
Earnings: [Date or No near-term risk]
Thesis: [1–2 sentence buy reason]
Risk: [Key risk factor]

Suggested entry zone: $xxx – $xxx
Stop-loss reference: $xxx (MA50 or close×(1-2×atr_pct), whichever is closer)
Target reference: $xxx (close + 3×ATR, or prior high high_20)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Summary table:

| Ticker | Sector | Pattern | Price | Vol Ratio | RSI | Sentiment | Earnings Risk | Score | Rating |
|--------|--------|---------|-------|-----------|-----|-----------|--------------|-------|--------|

---

## Important Notes

- Run after market close daily, screen for next-day candidates
- Technical patterns are probabilistic, not deterministic
- Always check earnings date before each trade — avoid holding through earnings naked
- Stop-loss is a hard rule, never cancel because "it feels like it'll bounce"
- Convert stop/target into concrete prices via atr_pct — don't give percentages only
- Small/mid-caps often lack news coverage — rely on technicals
- Weekends/holidays show the last trading day's data, this is normal
