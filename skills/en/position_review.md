# Position Review

Evaluate existing holdings: continue holding, add, reduce, or stop-loss exit.

## Data Sources

| Table | Purpose |
|-------|---------|
| `stock_fundamentals` | Latest financials / valuation |
| `stock_ohlcv_daily` | Current technical position |
| `news` | Major event monitoring |
| `stocks_meta` | Ticker metadata |

---

## Workflow

### Step 1: Read Position & Current State

```sql
-- Technical status
SELECT ticker, close, ma_20, ma_50, ma_200,
       rsi_14, dist_ma20_pct, dist_ma50_pct,
       vol_ratio, atr_pct, pct_chg
FROM stock_ohlcv_daily
WHERE ticker = '{ticker}'
  AND date = (SELECT MAX(date) FROM stock_ohlcv_daily);

-- Fundamental status
SELECT revenue_growth_yoy, earnings_growth_yoy,
       gross_margin, pe_ratio, ps_ratio, peg_ratio,
       analyst_target_price, analyst_rating, analyst_count,
       mspr, next_earnings_date, last_earnings_date, fcf_yield
FROM stock_fundamentals
WHERE ticker = '{ticker}';
```

---

### Step 2: Investment Thesis Validation

Evaluate whether the original buy thesis still holds:

**Growth Check**
- Revenue growth: accelerating / stable / decelerating?
- Profit margin: expanding / contracting?
- Growth rate still >15%?

**Valuation Change**
- Current PE vs expected PE at entry
- Price appreciation vs earnings growth: multiple expansion or earnings-driven?

**Thesis Judgment:**
- Thesis intact: earnings in-line or beat expectations → continue holding
- Thesis weakening: growth slowing but not broken → reduce and monitor
- Thesis broken: growth significantly below expectations → consider exit

---

### Step 3: Technical Health

**Healthy Signals:**
- Price > MA50 → medium-term trend intact
- Price > MA200 → long-term trend intact
- Pullback holds above MA50 → normal consolidation

**Warning Signals:**
- Price breaks below MA50 → reduce warning
- Price breaks below MA200 → strong reduce signal
- RSI < 35 + below MA50 → potential trend reversal

**Add Signal:**
```sql
dist_ma20_pct BETWEEN -0.03 AND 0.02
AND close > ma_50
AND vol_ratio < 0.8
AND rsi_14 BETWEEN 40 AND 55
```

---

### Step 4: News Scan

```sql
SELECT headline, sentiment_label, sentiment_score, published_at
FROM news WHERE ticker = '{ticker}'
  AND published_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
ORDER BY published_at DESC LIMIT 20;
```

Watch for:
- Management changes → high risk
- Regulatory / litigation risk
- Major competitor developments
- Positive catalysts

---

### Step 5: Output Conclusion

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Position Review: TICKER — Company Name
Review Date: xxxx-xx-xx
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Position Status
  Cost basis: $xxx   Current price: $xxx   P&L: +/-xx%

Investment Thesis Status: Intact / Weakening / Broken
  [1–2 sentence explanation]

Fundamental Changes
  Revenue growth: +xx% [accelerating / stable / decelerating]
  Gross margin: xx% [expanding / contracting]
  Analyst target: $xxx (upside: +xx%)

Technical Health
  MA50: $xxx [above / broken below]
  MA200: $xxx [above / broken below]
  Current position: [pullback to support / at highs / in trend]

Recent News
  [List important headlines, or "No significant events"]

━━━━━━━━━━━━━━━━━━
Action Recommendation
━━━━━━━━━━━━━━━━━━
[Choose one]

Continue Holding
   Reason: Thesis intact, technically healthy

Add Opportunity
   Suggested add zone: $xxx – $xxx
   Suggested add size: xx% of current position

Suggest Reduce
   Reduce to: xx% of total position
   Trigger: [immediately / on break below $xxx]

Suggest Exit
   Exit method: [full exit now / scale out gradually]

Watch & Wait
   Next review date: xxxx-xx-xx
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Trigger Events

| Event | Action |
|-------|--------|
| After quarterly earnings release | Must run review |
| Price breaks below MA50 | Run immediately |
| Major negative news | Run immediately |
| Position profit exceeds 50% | Consider partial take-profit |
| Held over 6 months | Periodic review |

## Core Principles

- Thesis broken > technical breakdown > stop-loss — any trigger requires re-evaluation
- Never delay stop-loss hoping "it'll come back"
- Taking profit is not a mistake; secure gains and wait for a better entry
- Only add when both "thesis validated + technical pullback" conditions are met

## Input Format

```
TICKER cost_basis shares
Example: RKLB 77.28 13
```

For staged entries, recalculate diluted cost:
```
Diluted cost = (remaining_shares × original_cost + new_shares × new_price) / total_shares
```
