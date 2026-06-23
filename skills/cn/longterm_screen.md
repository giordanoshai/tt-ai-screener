# 长线选股 (Long-Term Screen)

持仓周期 6-12 个月，基本面驱动。结合成长性、估值、护城河、内部人情绪和新闻情绪，筛选值得长期持有的优质股票。

## 数据来源

| 表名 | 用途 |
|------|------|
| `stock_fundamentals` | 财报/估值/分析师/内部人（主表） |
| `stock_ohlcv_daily` | 技术面确认入场时机 |
| `news` | 新闻情绪（含 AI 情绪评分） |
| `stocks_meta` | 股票元信息 |

---

## Workflow

### Step 1：成长性筛选

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

### Step 2：质量评级（0-100 分）

**成长性（40 分）**
- 营收增速 >30% → 40 分
- 营收增速 20-30% → 30 分
- 营收增速 10-20% → 20 分
- 利润增速 > 营收增速 → 额外 +5 分

**估值合理性（25 分）**
- PEG < 1.0 → 25 分
- PEG 1.0-1.5 → 18 分
- PEG 1.5-2.0 → 10 分
- PEG > 2.0 → 5 分
- 无 PEG 时用 PS 对行业中位数比较

**护城河（20 分）**
- 毛利率 >50% → 20 分
- 毛利率 40-50% → 15 分
- 毛利率 30-40% → 8 分

**内部人信号（15 分）**
- MSPR > 20 → 15 分
- MSPR 0-20 → 8 分
- MSPR < 0 → 0 分，标记风险
- MSPR 为 NULL → 中性处理，不扣分

---

### Step 3：分析师与市场验证

```sql
SELECT ticker, analyst_target_price, analyst_rating, analyst_count,
       d.close,
       (analyst_target_price - d.close) / d.close AS upside_pct
FROM stock_fundamentals f
JOIN stock_ohlcv_daily d ON f.ticker = d.ticker
  AND d.date = (SELECT MAX(date) FROM stock_ohlcv_daily)
WHERE f.analyst_target_price IS NOT NULL;
```

- `upside_pct > 0.15` → 保留
- `analyst_rating = 'buy'` → 加分
- `upside_pct < 0` → 标记高估风险
- 现价超分析师目标 >30% → 情绪/动能驱动，回撤风险高，降级

---

### Step 4：新闻情绪

```sql
SELECT ticker, headline, sentiment_label, sentiment_score, published_at
FROM news
WHERE ticker = '{ticker}'
  AND published_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
ORDER BY published_at DESC
LIMIT 30;
```

统计：
- 30 天 sentiment_score 均值
- 强负面（score < -0.3）数量
- 强正面（score > 0.3）数量

规则：
- 强负面 >= 2 条 → 降级，需人工复查
- 均值 > 0.15 → 情绪正面加分
- 均值 < -0.2 → 降级为 C

---

### Step 5：技术面入场时机

**买点 A：趋势中的回调**
```sql
AND dist_ma20_pct BETWEEN -0.05 AND 0.02
AND close > ma_50
AND rsi_14 BETWEEN 40 AND 55
```

**买点 B：突破整理区间**
```sql
AND close >= high_20 * 0.99
AND vol_ratio > 1.3
AND close > ma_50 AND ma_50 > ma_200
```

不在买点区间 → 加入观察清单，标记"等待回调"。
超买（RSI>75 或 dist_ma200>30%）→ 即使基本面好也标记"等回调"。

---

### Step 6：输出格式

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【长线候选】TICKER — 公司名        评级：A/B/C
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
成长性
   营收增速：+xx%   利润增速：+xx%   毛利率：xx%

估值
   PE：xx   PS：xx   PEG：xx
   分析师目标价：$xxx（空间 +xx%）  评级：buy/hold

内部人：MSPR xx（净买入/净卖出/无数据）

情绪：30 天均分 x.xx，正面 x 条 / 负面 x 条

财报日：xxxx-xx-xx

当前技术位置
   价格：$xxx  MA50 距离：+x.x%  MA200 距离：+x.x%  RSI：xx
   买点状态：现在是买点 / 等待回调至 $xxx

核心逻辑（2-3 句）：
   [为什么这只股票值得持有 6-12 个月]

主要风险：
   [估值风险/竞争风险/财报风险/未盈利等]

建议建仓方式：
   分批建仓 — 第一批 xx%，回调至 $xxx 加剩余
   止损参考：跌破 MA200（$xxx）考虑离场
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

汇总表：

| Ticker | 板块 | 营收增速 | 毛利率 | PE | PEG | 分析师空间 | MSPR | 情绪 | 技术位 | 综合评级 |
|--------|------|---------|-------|-----|-----|---------|------|------|-------|--------|

---

## 评级标准

- **A 级**：成长 + 估值 + 护城河都达标，有买点 → 可建仓
- **B 级**：大部分达标但有一项偏弱 → 持续跟踪
- **C 级**：基本面有隐忧或情绪偏负面 → 不操作

## 注意事项

- 高增长票常超买，基本面达标 ≠ 现在能买，必须看 Step 5 买点
- 未盈利成长股（ROE 为负、FCF 为负）需注明"烧钱扩张阶段"风险
- MSPR 常为 NULL（Finnhub 中小盘覆盖有限），缺失时按中性处理
- 每季财报后必须用 position-review 复核持仓逻辑
