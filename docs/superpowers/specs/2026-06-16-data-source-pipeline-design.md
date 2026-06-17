# 数据源与每日更新管道重设计

日期：2026-06-16

## 背景与问题

`tt-trading-mcp` 是一个开源自托管项目，当前数据管道假设用户只维护几个 ticker（`tickers.txt` 手填）：

- OHLCV：`db/fetch.py` / `db/update.py` 逐 ticker 调 `yf.download`
- Fundamentals：逐 ticker 调 Finnhub `/stock/metric`，5 天缓存
- News：逐 ticker 调 Finnhub `/company-news`，每天全量拉取
- 所有逻辑硬编码为美股（yfinance + Finnhub），无市场区分字段

开源用户的真实场景是：watchlist 可能只有几十个，但也可能导入几千支美股（剔除垃圾股后约 3000–4000 支处于"有意义"的可交易池），并希望未来扩展到 A 股等其他市场。当前架构在该规模下会遇到：

1. Finnhub 免费档 60 次/分钟限速 → 几千 ticker 的新闻/fundamentals 全量拉取需要 1 小时以上
2. 没有市场区分字段，无法插入其他市场的数据源
3. `tickers.txt` 不适合维护几千支股票的清单

## 目标

- 数据源可插拔：现在只接 yfinance(OHLCV) + Finnhub(fundamentals/news) 美股，但留好接口，未来加 A 股(如 akshare)不用动调度代码
- 几千支股票的每日更新能在合理时间内（几分钟到十几分钟）跑完，不触发 Finnhub 限速封禁
- 保持现状用户（只有几个 watchlist ticker）零感知，不引入新的必填配置

非目标：本次不实现 A 股数据源本身，只留接口；不做实时数据/分钟级数据；不做多用户/多租户。

## 架构设计

### 1. Provider 抽象层

```
data_sources/
  base.py          # OHLCVProvider, FundamentalsProvider, NewsProvider 三个 ABC
  us_market.py      # USMarketProvider：OHLCV→yfinance批量下载；Fundamentals/News→Finnhub
  registry.py       # 按 ticker 的 market 字段路由到对应 provider 实例
```

接口定义：

```python
class OHLCVProvider(ABC):
    def fetch_ohlcv(self, tickers: list[str], since: date | None = None) -> pd.DataFrame: ...

class FundamentalsProvider(ABC):
    def fetch_fundamentals(self, ticker: str) -> dict: ...

class NewsProvider(ABC):
    def fetch_news(self, ticker: str, days: int) -> list[dict]: ...
```

- `OHLCVProvider.fetch_ohlcv` 接受 **ticker 列表**而非单个 ticker，因为 yfinance 支持批量下载，这是解决吞吐问题的关键（不受 Finnhub 限速影响）
- `FundamentalsProvider` / `NewsProvider` 保持单 ticker 接口，因为 Finnhub 本身没有批量端点，限速发生在这一层，调度层负责控制调用节奏
- `registry.py` 按 `stocks_meta.market` 字段（如 `US`，未来加 `CN`）选择对应 provider 实例

### 2. Ticker 宇宙管理

`stocks_meta` 表新增两个字段：

- `market` VARCHAR（如 `US`，默认 `US`，向后兼容）
- `tier` VARCHAR（`core` | `universe`，默认 `core`）

语义：

- `tier='core'`：用户的 watchlist（`user_watchlist` 表）+ 有持仓的 ticker（`trades` 表中 status=open 的 ticker）。每天全量更新：OHLCV + fundamentals(7天缓存) + news(必拉)
- `tier='universe'`：导入的大盘股池（如 Russell 3000 成分股）。每天 OHLCV 全量批量更新 + fundamentals(7天缓存)，但 **news 只在触发异动门槛时才拉**

新增 `db/universe.py`：一次性/按需运行的导入脚本，从用户提供的 CSV（ticker 列表）批量写入 `stocks_meta`，`tier='universe'`。不内置任何外部指数源下载逻辑（避免引入对第三方网页结构的硬编码依赖），用户自己准备 CSV 即可。

`tickers.txt` 的职责收窄为 **core/watchlist 的初始种子文件**，现有 `config.load_tickers()` 行为不变（小白用户零改动）。

### 3. 每日更新调度（`db/update.py` 重写）

**OHLCV（全量，每天，core + universe 一视同仁）**

- 改为分批批量下载：每批 200 个 ticker 调一次 `yf.download(batch, group_by="ticker")`，而非逐 ticker 请求
- 批量结果按 ticker 拆分后复用现有 `_calc_indicators` 计算技术指标，写入逻辑不变

**Fundamentals（按 7 天缓存，core + universe 一视同仁）**

- 复用现有 stale-check 机制，`FUNDAMENTALS_STALE_DAYS` 由 5 改为 **7**
- 不区分 tier：fundamentals 变化本身就慢，不是限速瓶颈来源

**News（核心改动：tier + 异动门槛 gating）**

- `tier='core'`：每天必拉（与现状一致）
- `tier='universe'`：先用当天已经批量更新好的 OHLCV 计算异动，命中以下任一条件才进新闻拉取队列：
  - `abs(pct_chg) > 5%`（可配置，`config.py` 新增 `NEWS_MOVER_PCT_THRESHOLD`）
  - `vol_ratio > 2.0`（可配置，`config.py` 新增 `NEWS_MOVER_VOLRATIO_THRESHOLD`）
- 拉取仍走现有 `RATE_LIMIT_SLEEP` 限速 sleep；队列从几千缩小到几十~几百，总耗时从约 1 小时压缩到 10 分钟以内
- 失败处理沿用现状（try/except 跳过打日志），`ON CONFLICT DO NOTHING` 保证幂等，下次重跑自然补齐，不需要额外的 checkpoint 机制

**调度方式（Windows）**

- 新增 `db/run_daily_update.bat`：激活 venv + 跑 `python -m db.update`
- README 增加一段 `schtasks /create` 示例命令，供用户自行注册每日定时任务（如美股开盘前 5:00 AM），不在代码里写死调度逻辑

## 数据流示意

```
schtasks (每日触发)
  → run_daily_update.bat
    → update_ohlcv(core ∪ universe tickers)     批量 yfinance，不限速
    → update_fundamentals(core ∪ universe)      7天缓存，Finnhub限速队列
    → movers = 今日 OHLCV 中 pct_chg/vol_ratio 超门槛的 universe ticker
    → update_news(core ∪ movers)                 Finnhub限速队列，规模可控
```

## 测试策略

- 单元测试：`registry.py` 路由逻辑（给定 market 字段返回正确 provider 实例）
- 单元测试：异动门槛筛选函数（给定一批 OHLCV 行，正确筛出超阈值的 ticker）
- 集成测试：`update_ohlcv` 批量下载分批逻辑（mock yfinance，验证分批大小和合并结果）
- 手动验证：用现有 4 个测试 ticker（AAPL/NVDA/MSFT/TSLA）跑一次完整 `update.py`，确认行为与重写前一致（回归）

### 4. 板块 + 前端筛选层（请求侧）

本节是对第 3 节"更新侧 gating"的"请求侧"补充，解决用户在 Claude 对话或 Web UI 中调用选股时因候选池过大导致的 token / API 调用爆炸问题。

**核心思路**：先用纯本地 DB 查询按板块 + 技术条件缩小候选池 → 再对少量结果按需取新闻 / fundamentals（cache-first，没有就实时拉 Finnhub）。

#### 4.1 板块数据来源

`stocks_meta` 表中 `sector` 和 `industry` 字段已存在，但现有 `fetch_fundamentals` 里写入的是 `finnhubIndustry`（细分行业），字段映射需修正：

- `stocks_meta.sector` ← Finnhub `profile2.finnhubIndustry`（如 `Technology`，偏粗分类）
- `stocks_meta.industry` ← 同字段（暂时相同；未来可引入 SIC/GICS 细分）
- **不新增任何 API 调用**：`profile2` 已在 `fetch_fundamentals` 流程中拉取，只需确保写入时字段映射正确

#### 4.2 Web 筛选端点

`web/app.py` 新增两个端点：

```
POST /screen            纯本地 DB 查询，返回候选列表，不调用任何外部 API
GET  /ticker/{ticker}   单 ticker 详情：fundamentals + news，cache-first + Finnhub fallback
```

`POST /screen` 接受的筛选参数（完全基于现有 schema，不引入未存储的 MACD/KDJ/Bollinger）：

| 类别 | 参数 | 对应 DB 字段 |
|------|------|-------------|
| 板块 | `sectors[]` | `stocks_meta.sector` |
| 趋势 | `above_ma200`, `above_ma50`, `bull_alignment` | `close > ma_200` 等 |
| 距离 | `dist_ma20_min/max` | `dist_ma20_pct` |
| 动量 | `rsi_min/max` | `rsi_14` |
| 成交量 | `vol_ratio_min` | `vol_ratio` |
| 结构 | `near_20d_high`（close/high_20 ≥ 0.95） | `close`, `high_20` |
| 波动 | `atr_pct_min` | `atr_pct` |
| 涨跌 | `pct_chg_min/max` | `pct_chg` |
| 市场 | `market`（默认 `US`） | `stocks_meta.market` |
| 分层 | `tier`（`all`/`core`/`universe`） | `stocks_meta.tier` |

返回格式（轻量，**不含新闻 / fundamentals**）：`ticker`, `company_name`, `sector`, `close`, `pct_chg`, `rsi_14`, `vol_ratio`, `dist_ma20_pct`, `is_core`。

#### 4.3 单 ticker 详情端点（cache-first）

`GET /ticker/{ticker}` 执行顺序：

```
fundamentals:
  1. 查 stock_fundamentals WHERE ticker=? AND updated_at >= (today-7d)
  2. 命中 → 直接返回
  3. 未命中 → 实时 Finnhub /stock/metric → 写入 DB → 返回

news:
  1. 查 news WHERE ticker=? AND published_at >= (today-14d) ORDER BY published_at DESC LIMIT 20
  2. 命中 → 直接返回
  3. 未命中 → 实时 Finnhub /company-news → 写入 DB → 返回
```

单 ticker 按需拉取每次仅 1–2 个 Finnhub 请求，不触发限速，是"将批量成本推迟到用户实际需要时"的核心机制。

## 整体数据流（更新后）

```
每日定时任务（更新侧）
  → OHLCV 全量批量更新（yfinance 分批，不限速）
  → fundamentals 7天缓存更新（Finnhub 限速队列）
  → news：core必拉 + universe异动门槛拉（Finnhub 限速队列，规模可控）

用户请求（请求侧）
  → 前端/Claude 按板块 + 技术条件筛选 → POST /screen → 纯 DB 查询，返回轻量候选列表
  → 用户点开某支股票 → GET /ticker/{ticker}
      → fundamentals：DB 缓存（7天内） → 实时 Finnhub fallback → 写 DB
      → news：DB 缓存（14天内） → 实时 Finnhub fallback → 写 DB
```

## 测试策略

- 单元测试：`registry.py` 路由逻辑（给定 market 字段返回正确 provider 实例）
- 单元测试：异动门槛筛选函数（给定一批 OHLCV 行，正确筛出超阈值的 ticker）
- 单元测试：`POST /screen` 各筛选参数的 SQL 生成逻辑（覆盖 sector、bull_alignment、near_20d_high 等复合条件）
- 单元测试：`GET /ticker/{ticker}` cache-first 逻辑（新鲜缓存直接返回；过期缓存触发 Finnhub 调用）
- 集成测试：`update_ohlcv` 批量下载分批逻辑（mock yfinance，验证分批大小和合并结果）
- 手动验证：用现有 4 个测试 ticker（AAPL/NVDA/MSFT/TSLA）跑一次完整 `update.py`，确认行为与重写前一致（回归）

## 风险与权衡

- 异动门槛会漏掉"价格还没反应但已经有重大新闻"的极少数情况（用户已知悉并接受，这是新闻驱动行情通常伴随价量异动这一前提下的合理折衷）
- yfinance 批量下载在网络异常时整批失败的影响面比单 ticker 请求大，需要批次级别的 try/except（不细化到逐 ticker 重试，超出本次范围）
- `GET /ticker/{ticker}` 实时 Finnhub fallback 在极端情况下（API 超时/限速）会让前端等待；可在后续版本加超时上限 + 友好错误提示，当前版本直接返回空值即可
