# 持仓管理 (Position Review)

评估现有持仓是否继续持有、加仓、减仓或止损离场。

## 数据来源

| 表名 | 用途 |
|------|------|
| `stock_fundamentals` | 最新财报/估值 |
| `stock_ohlcv_daily` | 当前技术位置 |
| `news` | 重大事件监控 |
| `stocks_meta` | 股票元信息 |

---

## Workflow

### Step 1：读取持仓与当前状态

```sql
-- 技术面现状
SELECT ticker, close, ma_20, ma_50, ma_200,
       rsi_14, dist_ma20_pct, dist_ma50_pct,
       vol_ratio, atr_pct, pct_chg
FROM stock_ohlcv_daily
WHERE ticker = '{ticker}'
  AND date = (SELECT MAX(date) FROM stock_ohlcv_daily);

-- 基本面现状
SELECT revenue_growth_yoy, earnings_growth_yoy,
       gross_margin, pe_ratio, ps_ratio, peg_ratio,
       analyst_target_price, analyst_rating, analyst_count,
       mspr, next_earnings_date, last_earnings_date, fcf_yield
FROM stock_fundamentals
WHERE ticker = '{ticker}';
```

---

### Step 2：投资逻辑验证

评估买入时的逻辑是否仍然成立：

**成长逻辑检查**
- 营收增速：加速 / 持平 / 减速？
- 利润率：扩张 / 收窄？
- 增速是否仍 >15%？

**估值变化**
- 当前 PE vs 买入时预期
- 价格涨幅 vs 盈利增幅：估值扩张还是业绩驱动？

**逻辑评判：**
- 逻辑完好：业绩符合或超预期，继续持有
- 逻辑弱化：增速放缓但未破坏，减仓观察
- 逻辑破坏：增速大幅低于预期，考虑离场

---

### Step 3：技术面健康度

**健康信号：**
- 价格 > MA50 → 中期趋势完好
- 价格 > MA200 → 长期趋势完好
- 回调不破 MA50 → 正常整理

**预警信号：**
- 价格跌破 MA50 → 减仓警告
- 价格跌破 MA200 → 强烈减仓信号
- RSI < 35 + 跌破 MA50 → 趋势可能逆转

**加仓信号：**
```sql
dist_ma20_pct BETWEEN -0.03 AND 0.02
AND close > ma_50
AND vol_ratio < 0.8
AND rsi_14 BETWEEN 40 AND 55
```

---

### Step 4：新闻扫描

```sql
SELECT headline, sentiment_label, sentiment_score, published_at
FROM news
WHERE ticker = '{ticker}'
  AND published_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
ORDER BY published_at DESC
LIMIT 20;
```

关注：
- 管理层变动 → 高风险
- 监管/诉讼风险
- 竞争对手重大进展
- 正面催化剂

---

### Step 5：输出结论

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
持仓复查：TICKER — 公司名
复查日期：xxxx-xx-xx
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
持仓状态
  买入价：$xxx   当前价：$xxx   盈亏：+/-xx%

投资逻辑状态：完好 / 弱化 / 破坏
  [1-2 句说明]

基本面变化
  营收增速：+xx% [加速/持平/减速]
  毛利率：xx% [扩张/收窄]
  分析师目标价：$xxx（上涨空间：+xx%）

技术面健康度
  MA50：$xxx [在上方/跌破]
  MA200：$xxx [在上方/跌破]
  当前位置：[回调至支撑/在高位/趋势中]

近期新闻
  [列出重要新闻，无则"无重大事件"]

━━━━━━━━━━━━━━━━━━
操作建议
━━━━━━━━━━━━━━━━━━
[选一个]

继续持有
   理由：逻辑完好，技术面健康

加仓机会
   建议加仓价格区间：$xxx - $xxx
   建议加仓比例：现有仓位的 xx%

建议减仓
   建议减至：总仓位的 xx%
   减仓触发条件：[立即 / 跌破 $xxx 时]

建议离场
   离场方式：[立即清仓 / 分批减仓]

观察等待
   下次复查时间：xxxx-xx-xx
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 触发时机

| 事件 | 操作 |
|------|------|
| 每季财报发布后 | 必须跑一次 |
| 价格跌破 MA50 | 立即跑 |
| 出现重大负面新闻 | 立即跑 |
| 持仓盈利超 50% | 考虑部分止盈 |
| 距买入超 6 个月 | 定期复查 |

## 核心原则

- 逻辑破坏 > 技术面破坏 > 止损，任何一个触发都要重新评估
- 不要用"涨回来再卖"拖延止损
- 止盈不是错误，落袋为安后可以等更好买点
- 加仓只在"逻辑验证 + 技术回调"同时满足时进行

## 输入格式

```
TICKER 成本价 股数
例：RKLB 77.28 13
```

分批操作时重新计算摊薄成本：
```
摊薄成本 = (剩余股数 x 原成本 + 加仓股数 x 加仓价) / 总股数
```
