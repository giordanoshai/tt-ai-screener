# Long-Term Stock Screening

Holding period: 6–12 months, fundamentals-driven. Combine growth, valuation, moat, insider sentiment, and news sentiment to identify quality stocks worth holding long-term.

## Data Sources

| Table | Purpose |
|-------|---------|
| `stock_fundamentals` | Financials / valuation / analyst / insider (primary) |
| `stock_ohlcv_daily` | Technical confirmation for entry timing |
| `news` | News sentiment (with AI sentiment scores) |
| `stocks_meta` | Ticker metadata |

---

## Workflow

### Step 1: Growth Screening

```sql
SELECT f.ticker, m.company_name, m.sector,
       f.revenue_growth_yoy, f.earnings_growth_yoy, f.gross_margin,
       f.pe_ratio, f.ps_ratio, f.pb_ratio, f.peg_ratio,
       f.analyst_rating, f.analyst_target_price, f.analyst_count,
       f.mspr, f.next_earnings_date, f.last_earnings_date,
       d.close, d.ma_50, d.ma_200, d.rsi_14,
       d.dist_ma20_pct, d.dist_ma50_pct, d.vol_ratio, d.atr_pct
FROM stock_fundamentals f
JOIN stocks_meta m ON f.ticker = m.ticker
JOIN stock_ohlcv_daily d ON f.ticker = d.ticker
  AND d.date = (SELECT MAX(date) FROM stock_ohlcv_daily)
WHERE f.updated_at >= CURRENT_DATE - INTERVAL '7 days'
  AND f.revenue_growth_yoy > 0.10
  AND f.earnings_growth_yoy > 0.10
  AND f.gross_margin > 0.30
  AND f.pe_ratio < 60
  AND d.close > d.ma_200
ORDER BY f.revenue_growth_yoy DESC
LIMIT 50;
```

---

### Step 2: Quality Rating (0–100 points)

**Growth (40 pts)**
- Revenue growth >30% → 40 pts
- Revenue growth 20–30% → 30 pts
- Revenue growth 10–20% → 20 pts
- Earnings growth > revenue growth → bonus +5 pts

**Valuation Reasonableness (25 pts)**
- PEG < 1.0 → 25 pts
- PEG 1.0–1.5 → 18 pts
- PEG 1.5–2.0 → 10 pts
- PEG > 2.0 → 5 pts
- If PEG unavailable, compare PS to sector median

**Moat (20 pts)**
- Gross margin >50% → 20 pts
- Gross margin 40–50% → 15 pts
- Gross margin 30–40% → 8 pts

**Insider Signal (15 pts)**
- MSPR > 20 → 15 pts
- MSPR 0–20 → 8 pts
- MSPR < 0 → 0 pts, flag risk
- MSPR is NULL → neutral, no penalty

---

### Step 3: Analyst & Market Validation

```sql
SELECT ticker, analyst_target_price, analyst_rating, analyst_count,
       d.close,
       (analyst_target_price - d.close) / d.close AS upside_pct
FROM stock_fundamentals f
JOIN stock_ohlcv_daily d ON f.ticker = d.ticker
  AND d.date = (SELECT MAX(date) FROM stock_ohlcv_daily)
WHERE f.analyst_target_price IS NOT NULL;
```

- `upside_pct > 0.15` → keep
- `analyst_rating = 'buy'` → bonus
- `upside_pct < 0` → flag overvaluation risk
- Price exceeds analyst target by >30% → sentiment/momentum driven, high pullback risk, downgrade

---

### Step 4: News Sentiment

```sql
SELECT ticker, headline, sentiment_label, sentiment_score, published_at
FROM news
WHERE ticker = '{ticker}'
  AND published_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
ORDER BY published_at DESC
LIMIT 30;
```

Aggregate:
- 30-day average sentiment_score
- Count of strong negatives (score < -0.3)
- Count of strong positives (score > 0.3)

Rules:
- Strong negatives >= 2 → downgrade, needs manual review
- Average > 0.15 → positive sentiment bonus
- Average < -0.2 → downgrade to C

---

### Step 5: Technical Entry Timing

**Entry A: Pullback within uptrend**
```sql
AND dist_ma20_pct BETWEEN -0.05 AND 0.02
AND close > ma_50
AND rsi_14 BETWEEN 40 AND 55
```

**Entry B: Breakout from consolidation**
```sql
AND close >= high_20 * 0.99
AND vol_ratio > 1.3
AND close > ma_50 AND ma_50 > ma_200
```

Not at entry point → add to watchlist, mark "wait for pullback".
Overbought (RSI>75 or dist_ma200>30%) → even with strong fundamentals, mark "wait for pullback".

---

### Step 6: Output Format

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【Long-Term Candidate】TICKER — Company Name        Rating: A/B/C
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Growth
   Revenue growth: +xx%   Earnings growth: +xx%   Gross margin: xx%

Valuation
   PE: xx   PS: xx   PEG: xx
   Analyst target: $xxx (upside +xx%)  Rating: buy/hold

Insider: MSPR xx (net buying / net selling / no data)

Sentiment: 30-day avg x.xx, positive x / negative x

Earnings date: xxxx-xx-xx

Current Technical Position
   Price: $xxx  MA50 dist: +x.x%  MA200 dist: +x.x%  RSI: xx
   Entry status: Entry point now / Wait for pullback to $xxx

Core Thesis (2–3 sentences):
   [Why this stock is worth holding for 6–12 months]

Key Risks:
   [Valuation risk / competition / earnings risk / pre-profit, etc.]

Suggested Entry Plan:
   Scale in — first batch xx%, add remaining on pullback to $xxx
   Stop-loss reference: consider exit if breaks below MA200 ($xxx)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Summary table:

| Ticker | Sector | Rev Growth | Gross Margin | PE | PEG | Analyst Upside | MSPR | Sentiment | Technical | Overall |
|--------|--------|-----------|-------------|-----|-----|---------------|------|-----------|-----------|---------|

---

## Rating Criteria

- **A**: Growth + valuation + moat all qualify, at entry point → actionable
- **B**: Most criteria met but one is weak → continue tracking
- **C**: Fundamental concerns or negative sentiment → no action

## Important Notes

- High-growth stocks are often overbought; strong fundamentals ≠ buy now — always check Step 5 entry
- Pre-profit growth stocks (negative ROE, negative FCF) must note "cash-burn expansion phase" risk
- MSPR is often NULL (limited Finnhub coverage for small/mid-caps), treat missing as neutral
- After each quarterly earnings, run position-review to re-validate holding thesis
