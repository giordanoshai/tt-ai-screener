# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**tt-trading-mcp** is a self-hostable MCP (Model Context Protocol) server for personal stock trading analysis. It connects to Claude.ai for long-term investing, swing trading, position tracking, and trading psychology journaling.

The design philosophy: **Claude is the UI**. Users interact primarily through Claude.ai conversations backed by this MCP server (a small local web dashboard exists for manual browsing).

**This project is a read-only consumer.** All data collection — daily/hourly OHLCV, fundamentals, news, sentiment scoring, table creation and migrations — lives in a separate repo, **`tt-data-hub`**, which runs on the server against the same self-hosted Supabase/Postgres instance. Nothing here writes market data; the only writes are the user's own watchlist and positions. See "Data Sources" below.

Two markets are in the same tables: **`US`** (US stocks, USD) and **`CN`** (A-shares, CNY, tickers carry an exchange suffix like `600519.SH` / `000001.SZ` / `430139.BJ`). Every query against a market-data table must carry a `market` condition, and USD/CNY amounts must never be summed together.

---

## MCP Tools Reference

This is the canonical list of tools this server exposes (12 tools). Each tool maps to a function in `tools/`.

### 五个场景工具（本轮新增，围绕实际交易场景，工具内部算完直接返回结论，不调用大模型）
| Tool | Params | Description |
|---|---|---|
| `review_positions` | `ticker=None` | 我手上这几只票现在什么情况，该不该动。给每只持仓算盈亏、技术面位置、止损该放哪、有没有跌破、几天后财报、近期重大消息、期权/资金流向（A股换成主力资金和龙虎榜），**按需要动手的紧急程度排序**（跌破止损排最前，只是涨得多的排后面）。汇总按币种分开，不填 `ticker` 就看全部持仓 |
| `scan_swing` | `market='US'`, `horizon='short'`, `scope='pool'`, `limit=15` | 盘后扫一遍，找值得做的波段机会。每只入选票给形态和理由、买入区间、按 ATR 算的止损、2倍/3倍风险距离的目标价。`horizon`: `short` 短波段（3天~2周）/ `mid` 中线（2周~2月）/ `both`。`scope`: `pool` 持仓+自选 / `full` 全市场（建议每天盘后跑 pool，周一周四跑 full）。`limit` 上限 30。美股入选的票另带 IV 排名和期权量比——同样看多的判断，期权贵的时候该用正股进 |
| `analyze_ticker` | `ticker`(required), `horizon='both'` | 单独一只票的完整体检：日线趋势 + 小时线买卖点位（原 `get_hourly_setup` 并入这里）、基本面（仅中线档展开）、近两周新闻情绪、期权资金流向、**期权贵不贵（IV 排名）+ 期权量比 + 未平仓量变化**、gamma 墙、财报日、内部人买卖、国会交易（汇总 + 最近三笔）；A股换成主力资金、龙虎榜、板块强弱。走实时接口，取不到自动退回数据库缓存 |
| `detect_catalysts` | `scope='positions'`, `hours=24`, `min_significance='Medium'` | 最近出了什么事，跟我的票有没有关系，股价反应过了没有（已经涨完的利好是风险，纹丝不动的利好才是机会）。一条新闻只算一个事件，不按命中的票数重复扇出；会标出是不是「大盘综述」并降低排序优先级。`scope`: `positions`/`watchlist`/`market`。另附一条大盘背景：全市场高管近 5 天是在买还是在卖 |
| `watch_positions` | `hours=24` | 持仓上有没有需要立刻处理的信号：价格触发（跌破均线/止损、创20日新低）、财报临近、期权异动、突发消息，外加 UW 实时警报命中持仓的部分。**没事就说没事**，不制造噪音；同一只票的多笔期权异动合并成一条，不按笔数刷屏 |

### Screening
| Tool | Params | Description |
|---|---|---|
| `screen_stocks` | `screen_type='longterm'`, `market='US'`, `sector`, `min_market_cap`, `max_pe`, `min_growth`, `min_margin`, `min_rsi`, `max_rsi`, `above_ma`, `limit=20` | Parameterized stock screener. **At least one filter required** (no filter = rejected). `screen_type`: `longterm`/`swing` determines scoring. `market`: `US`/`CN`. Filters: sector name, market cap (billions), PE cap, growth % floor, margin % floor, RSI range, MA price floor (20/50/200). Result count capped at 30 |

### Watchlist
| Tool | Params | Description |
|---|---|---|
| `list_watchlist` | — | List all watchlist tickers with latest price, daily change, and technicals |
| `add_to_watchlist` | `ticker`(required) | Add ticker to watchlist (rejected if the ticker is not yet in `stocks_meta`) |
| `remove_from_watchlist` | `ticker`(required) | Remove ticker from watchlist |

### Positions
| Tool | Params | Description |
|---|---|---|
| `list_positions` | — | List all positions with current price, P&L (% and $). Summary is **per currency** (`{"USD": {...}, "CNY": {...}}`) — no cross-currency total |
| `add_position` | `ticker`(required), `avg_cost`(required), `shares`(required), `market='US'` | Add or update a position. `market` (`US`/`CN`) decides the currency. Primary key is `ticker` alone, so the same ticker cannot be held in two markets |
| `remove_position` | `ticker`(required) | Remove a position |

**下线的三个工具**：`analyze_watchlist`（被 `scan_swing(scope="pool")` 取代，函数已删除）、
`analyze_positions`（被 `review_positions` 取代，函数已删除）、`get_hourly_setup`
（逻辑并入 `analyze_ticker`，`get_hourly_setup` 这个 JSON 包装函数还在
`tools/hourly.py` 里，只有测试在用了——2026-09-03 查过，网页面板一处都没引用，
早先这里写「网页面板仍在用」是过期的）。

---

## Architecture

```
tt-trading-mcp/
  main.py                # Unified entrypoint (web + MCP, or MCP stdio)
  watchdog.py            # 盯盘守护进程，独立于 MCP 跑。python watchdog.py [--once]
  server.py              # MCP server — registers all 12 tools, token auth
  config.py              # Postgres URLs, fallback AI env vars, MCP port/token
  ai_client.py           # OpenAI/Anthropic-compatible AI client; reads config/models.json
  config/
    models.json          # AI model list (NO keys — each entry names an env var)
    skill_prompts.json   # Custom skill prompts; absent until the UI saves one
  sql/
    001_analysis_history.sql   # The one table this repo owns
  db/
    pg.py                # THE ONLY database gateway (read + write)
  core/                  # 取数层，给下面 tools/ 的五个场景工具共用。纯 Python + SQL，不碰大模型
    snapshot.py           # 日线技术面快照（均线/RSI/量比/ATR），5 个工具里 4 个要它
    scoring.py             # 形态识别、分档评分、止损/目标价——纯函数，不碰数据库
    catalyst.py             # 消息面：新闻、财报日、期权异动、内部人买卖、gamma 墙
    fundamental.py           # 基本面取数，只有中线档才该调它（短线禁用，见下方「短线禁用字段」）
    regime.py                 # 市场环境和数据新鲜度（大盘状态、行情陈旧检测）
    cn_signals.py              # A股独立信号：主力资金、龙虎榜、板块排名、涨停情绪
    options.py                  # 期权和资金面：期权贵不贵（IV 排名）、期权量比、
                                 # 未平仓量变化（谁在建新仓）、全市场内部人买卖。
                                 # 只有美股（UW 不做 A股），覆盖面每天四五百只
    uw.py                       # Unusual Whales 实时接口客户端（期权异动/警报/美股小时线），
                                 # 带浏览器 UA。小时线用 3M 档，只留盘中那一档，取不到返回空
    akshare.py                   # 自建 AKTools 实时客户端。只做 A股小时线，取不到返回空
    levels.py                    # 关键位计算：从日线找头顶的坎和脚下的坎。
                                 # 【用收盘价找位，不用最高价】——插针不算数，
                                 # 收得住才算数。纯函数，不碰库不碰网
    session.py                   # 交易时段：A股只有盘中，美股有盘前盘后，
                                 # 一天要盯 20 小时。冬令时美股三段各顺延 1 小时
    quotes.py                    # 实时价，两个市场两条路：A股走新浪全市场快照
                                 # （一趟 15 秒拿 5555 只），美股走 UW 1 分钟线
                                 # 并发拉。【AKTools 上东财源全部 500，只能用新浪】
    alerts.py                    # 报警判定：只推有效突破/假突破/跌破三类，
                                 # 预警穿越触发。盘中量比必须按时间折算
    notify.py                    # Telegram 推送，_post 单独抽出来给测试换
  tools/
    compact.py           # 交出去之前压一压：小数按字段截断、时间只留日期。
                         # 量级混着放，加字段前先确认是百分点还是小数
    screening.py         # screen_stocks (parameterized screener, filter required)
    watchlist.py         # list/add/remove watchlist
    positions.py         # list/add/remove positions
    hourly.py            # 小时线计算逻辑，被 analyze_ticker 调用，不再单独暴露为 MCP 工具
                         # 【两个市场都走实时】，取不到才退回库里，返回体带 source 字段
                         # 标明走的哪条路（uw_live / akshare_live / db）
                         # A股（2026-09-01）：库里只覆盖活跃池约 150 只，而活跃池是按
                         # 「站上 50 日均线 + 放量」挑的，跟「用户想看哪只票」是两回事
                         # ——实测茅台/五粮液/宁德/比亚迪库里一根都没有
                         # 美股（2026-09-03）：tt-data-hub 把 us_hourly 改成按需跑、
                         # 撤了 09:40 的定时，库里那份从此不再自己更新，会一天比一天旧；
                         # 覆盖面同样只有活跃池那 247 只
    review_positions.py  # review_positions —— 我手上这几只票该不该动
    scan_swing.py         # scan_swing —— 盘后找值得做的波段机会
    analyze_ticker.py      # analyze_ticker —— 单独一只票的完整体检
    detect_catalysts.py     # detect_catalysts —— 出了什么事，跟我有没有关系
    watch_positions.py       # watch_positions —— 持仓上有没有要立刻处理的信号
  skills/
    __init__.py          # 网页面板分析页的取数层（MCP 工具已经不用它了，但网页仍依赖它）
    en/, cn/             # Skill prompts per language:
                         #   longterm_screen.md / swing_screen.md / position_review.md
  web/
    app.py               # FastAPI web dashboard
    templates/           # home / index / screener / analysis / history / settings
  tests/
  requirements.txt
```

**Key design rule:** All filtering and aggregation happens inside the tool function (database or Python side). Tools return lightweight summaries to Claude — never raw full tables. This keeps token usage low.

### `db/pg.py` — the only DB gateway

Reads and writes use **separate database accounts**, physically isolated:

- Reads go through `readonly_user`, and the session is additionally set to a read-only transaction, so even a buggy write statement is rejected by the database itself.
- Writes go through `write_user` and are only for `user_watchlist` / `user_positions`.
- Postgres `numeric` is converted to `float` at this layer (a `Decimal` would blow up `json.dumps`).

API: `pg.query(sql, params) -> list[dict]`, `pg.query_one(...) -> dict | None`, `pg.execute_write(...) -> rowcount`, `pg.fetch_write(...)` for `RETURNING`.

**Five rules every query must follow** (each one has already broken this codebase once):

1. **Placeholders are `%s`, not `?`.** psycopg2, not DuckDB. A leftover `?` raises `syntax error at or near "?"`.
2. **Always filter on `market`.** `stock_ohlcv_daily` holds ~2.75M US rows and ~1.05M CN rows in one table; a missing `market` condition silently mixes A-shares into US results.
3. **Never take the latest date with a correlated subquery.** `o.date = (select max(date) from stock_ohlcv_daily where ticker = o.ticker)` measured as a 2.75M-row sequential scan (~6.7s). Use one of two shapes: for a handful of tickers, a per-ticker `order by date desc limit 1` index scan (also keeps halted/delisted names); for a full-pool scan, fetch the market's latest trading day in a separate small query first, then compare for equality (~109ms).
4. **`stock_fundamentals` has many rows per ticker** (PK is `(ticker, updated_at)`; ~2,135 tickers spread over ~29,943 rows). Always dedupe with `distinct on (ticker) ... order by ticker, updated_at desc`.
5. **`stock_ohlcv_min` has three timeframes sharing one table** — `1h` / `1m` / `5m`, split by the `timeframe` column. Minute-level data alone is ~20M rows / 9.3GB, so a query missing a `timeframe` condition is a disaster.

### 比率字段的单位约定：百分点 vs 小数

`core/snapshot.py`（日线技术面快照）和 `core/fundamental.py`（基本面取数）这两个
模块**对外交出来的比率字段用的是百分点**——比如均线偏离度 `dist_ma20_pct` 是
`3.18` 这种量级（代表 3.18%），不是 `0.0318`。

这跟 `skills/__init__.py`、`web/`、`tools/screening.py` 里那些**直接查数据库**
的老代码不一样：数据库里 `stock_ohlcv_daily.dist_ma20_pct` 这些列本身存的就是
小数（`0.0318`），老代码直接拿小数用，这是对的，不用改。新的 `core/` 层是在
`snapshot.py`/`fundamental.py` 内部把小数换算成了百分点再往外传，是**故意的
设计选择**（给五个场景工具的 JSON 输出用，可读性更好），但这意味着：

- **看到一个比率字段，先看它是从 `core/` 来的还是从数据库/`skills/` 来的**，
  两边量级差 100 倍，混用会出严重的错。
- 这个坑已经在本轮开发中真实炸过三次：短线三种形态判断里有一种因为门槛值
  按小数量级写、实际收到百分点量级的数，永远不触发；中线基本面评分（满分
  35 分）里有 12 分因为同样的量级错位全场归零；给用户展示的营收增速把
  `+106%` 印成了 `+1.1%`（多除了一次 100）。
- 新增或修改 `core/` 下的模块、或者对接它们的返回值时，**先确认这个字段是
  百分点还是小数，不要想当然**。

---

## Data Sources

Market data comes from Postgres; configuration comes from files in the repo and from
`.env`. Nothing comes from a local database file any more — **there is no DuckDB**.
`trading.duckdb` is a dead leftover; no code reads it, and no code should.

| What | Where it lives | Who writes it |
|---|---|---|
| Prices/OHLCV (daily + intraday), fundamentals, news + sentiment, Unusual Whales options data | Self-hosted Supabase/Postgres | **`tt-data-hub` only.** This repo reads it through `readonly_user` |
| Watchlist, positions | Same Postgres (`user_watchlist`, `user_positions`) | **This repo**, through `write_user` |
| Analysis history | Same Postgres (`analysis_history`) | **This repo**, through `write_user` |
| AI model configuration | `config/models.json` (a file, not a table) | Edited by hand |
| API keys for those models | `.env` | Edited by hand |
| Custom skill prompts | `config/skill_prompts.json` (a file, not a table) | Written by the Settings page |
| Default skill prompts | `skills/<lang>/<skill>.md` | Edited by hand |
| Output language | `LANG_UI` env var — fixed, not switchable at runtime | Edited by hand |

### Market data: read-only, collected elsewhere

**Collection lives in `tt-data-hub`, not here.** That repo owns the ingest jobs, the
schema, the migrations, and the system cron on the server. Do not add fetching, table
creation, or migrations to this repo. There is no `/update` endpoint and no update
button — those were removed, not stubbed, because a button that only apologises is
worse than no button. To see how the last ingest run went, read `ingest_runlog`
(surfaced on the home page and at `GET /api/update/last`).

### Writes from this repo: exactly three tables

`user_watchlist`, `user_positions`, and `analysis_history`. Everything else goes
through the read-only account, whose session is additionally set to a read-only
transaction. `analysis_history` is the newest of the three: every AI analysis run
from the web UI (both `POST /analyze` and the streaming `POST /analyze/stream`) is
saved there, and the History page reads it back. **A failed history write must never
fail the analysis** — the answer is already computed and on its way to the user, so a
failure there is logged and swallowed.

### AI models: `config/models.json`

The model list used to be an `ai_models` table in the old DuckDB. It was missed
during the migration and does not exist in Postgres, which is why AI analysis
silently reported "no model configured" for a while. It now lives in
`config/models.json`:

```jsonc
{
  "models": [
    {
      "id": "qwen-max",                  // stable key; what /analyze's `model` param takes
      "display_name": "Qwen3.7 Max",
      "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "model_id": "qwen3.7-max",         // what goes in the API request body
      "api_format": "openai",            // openai | anthropic
      "role": "analysis",                // analysis | sentiment | both
      "supports_thinking": true,
      "is_default_analysis": true,       // picked when no id is given
      "is_default_sentiment": false,
      "enabled": true,                   // false = hidden everywhere
      "api_key_env": "DASHSCOPE_API_KEY" // NAME of an env var, never the key itself
    }
  ]
}
```

**No secret ever goes in this file.** `api_key_env` names an environment variable
(`ANTHROPIC_API_KEY`, `DASHSCOPE_API_KEY`, `OPENAI_API_KEY`, `AI_API_KEY`, …) and
`ai_client.get_model()` resolves it with `os.getenv` at call time. The file is read
fresh on every call, so an edit takes effect without a restart.

The Settings page shows this list **read-only** — adding, removing, or changing the
default model means editing the file. The old `POST /models` and `DELETE /models/{id}`
endpoints are gone.

If `config/models.json` is missing, unreadable, or has no models, `ai_client` falls
back to the single-model-per-role env vars (`AI_API_BASE`/`AI_API_KEY`/`AI_MODEL` and
`SENTIMENT_API_BASE`/`SENTIMENT_API_KEY`/`SENTIMENT_MODEL`).

### Skill prompts: `skills/<lang>/*.md`, overridable in `config/skill_prompts.json`

`load_skill_prompt(skill, lang)` returns the custom prompt if
`config/skill_prompts.json` has one, otherwise the bundled markdown file. The JSON is
`{"<skill>": {"<lang>": "<prompt>"}}`; a missing file, a broken file, or a blank entry
all fall back to the markdown without erroring. The Settings page writes it (Save) and
removes entries from it (Reset to Default). The `skill_prompts` table it replaced does
not exist in Postgres.

### Language: fixed

`LANG_UI` (`en` / `cn`, default `en`) decides the analysis output language and which
`skills/<lang>/` directory is used. **Runtime language switching was removed** — the
`app_settings` table it was stored in does not exist in Postgres, and the feature is
not wanted. There is no `/settings/lang` endpoint and no switch in the UI.

### `.env`

```bash
PG_READ_URL=...            # Postgres, readonly_user  (all market data)
PG_WRITE_URL=...           # Postgres, write_user     (watchlist + positions + history)
WATCHLIST_USER_ID=...      # uuid; user_watchlist.user_id is NOT NULL
ANTHROPIC_API_KEY=...      # AI keys, referenced by name from config/models.json
DASHSCOPE_API_KEY=...
OPENAI_API_KEY=...
AI_API_BASE=...            # fallback single-model config, only used if models.json is empty
SENTIMENT_API_BASE=...     # same, for sentiment scoring
LANG_UI=en                 # en | cn, output language (fixed, not switchable in the UI)
AKSHARE_BASE_URL=...       # 自建 AKTools（AKShare 的 HTTP 封装），跟 tt-data-hub
                            # 同一个服务，形如 http://127.0.0.1:8080/api/public。
                            # 只用于 A股小时线的【实时】取数，见 core/akshare.py。
                            # 不配也不会坏，只是 A股小时线退回库里那 150 只
TG_BOT_TOKEN=...           # 盯盘推送用的 Telegram 机器人（复用已有的那个）
TG_CHAT_ID=...             # 推给谁
TG_PROXY=...               # 服务器在国内，Telegram 要走代理，形如 http://127.0.0.1:7890
WATCHDOG_INTERVAL=60       # 盯盘每轮间隔秒数
WATCHDOG_WARN_DIST=0.01    # 预警线距关键位多远（小数），默认提前 1%
WATCHDOG_PREMARKET=1       # 美股盘前要不要盯，默认要
WATCHDOG_STATE_FILE=...    # 盯盘的去重状态文件（哪个位哪天报过、今天推了几条）。
                            # 不配就落在仓库根目录的 .watchdog_state.json，
                            # 已在 .gitignore 里——每台机器各跑各的，不该进版本库。
                            # 进程重启靠它才不会重复轰炸用户
UW_API_TOKEN=...           # Unusual Whales（期权异动 + 美股小时线）。历史遗留键名
                            # UW_T_ENDPOINT / UW_T_KEY 也认，config.py 两套都读。
                            # 不配也不会坏，但美股小时线会退回库里那 247 只，
                            # 而那份数据自 2026-09-03 起不再每天更新（上游改成了按需）
MCP_PORT=8766
MCP_TOKEN=...              # auto-generated on first run
MCP_ALLOWED_HOSTS=...      # 逗号分隔。MCP SDK 的 Host 白名单默认只放行
                            # 127.0.0.1/localhost/[::1]，局域网 IP 和隧道域名
                            # 不加进来就是 421。端口可用 * 通配，例：
                            # 192.168.1.38:*,mcp.你的域名
```

**Core database tables** (self-hosted Postgres; market-data schema owned by `tt-data-hub`):

| Table | Notes |
|---|---|
| `stocks_meta` | Ticker metadata: `company_name`, `sector`, `industry`, `exchange`, **`market`**, `currency`. **`sector` is almost always empty**: measured 2026-08-31, 2,020 of 34,683 rows have one (5.8%) — all of them US (2,020 / 29,133 = 6.9%); every one of the 5,550 CN rows is empty. Anything that needs a sector must fall back to `ticker_over20b.sector` the way `tools/screening.py` does |
| `stock_ohlcv_daily` | Daily OHLCV + RSI14 + `ma_20/50/200` + `dist_ma20_pct`/`dist_ma50_pct` + `high_20` + `vol_ratio` + `atr_pct`. Keyed `(ticker, date)`, carries **`market`** |
| `stock_ohlcv_min` | Intraday bars, **three timeframes share this table** — split by the `timeframe` column (`'1h'` / `'1m'` / `'5m'`). Minute data is ~20M rows / 9.3GB, so a query missing `timeframe = '1h'` is a disaster. `ts` is the bar's **start** time, stored as `timestamptz` (convert to `America/New_York` or `Asia/Shanghai` for display). US bars carry a `session` of `RTH`/`ETH` |
| `v_ohlcv_hourly` | Convenience view over the `'1h'` rows. `tools/hourly.py` deliberately queries the base table instead — the view hides `session` and `timeframe`, and Postgres inlines it anyway (identical plan, cost 6.33) |
| `stock_fundamentals` | Valuation, growth, margins, analyst ratings, earnings dates, insider MSPR, short interest. **Many rows per ticker** (see rule 4 above). Currently **US only** — CN tickers legitimately come back with empty fundamentals |
| `news` | Articles with AI `sentiment_label` + `sentiment_score`. **No `ticker` column** — tickers live in `related_tickers` (`text[]`, GIN-indexed, so match with `&&`). **No `headline` column** — the title is in `title` |
| `cn_news` | **A股新闻**，`tt-data-hub` 的 `cn_news` 任务写的（2026-09-01 新增）。列跟 `news` 刻意对齐，`core/catalyst.py` 把两张表 `union all` 起来查——只查 `news` 的话 A股等于没有消息面（实测 82387 条里带 A股代码的只有 218 条，0.26%）。`significant` 是关键词规则打的（High/Medium/Low），**`sentiment_label` 恒为 NULL**，见 tt-data-hub 的 `sql/006_cn_news.sql` |
| `uw_screener_daily` | UW 每日筛选快照，每天 500 只，一个 `payload` jsonb。**IV 排名**（期权现在贵不贵，0~100）、30 天隐含波动率、财报预期波动（`implied_move` 是**美元金额**不是百分比）、板块、市值。2026-09-03 起由 `core/options.py` 读 |
| `uw_options_volume_daily` | 整只票的期权成交量、溢价、未平仓量，还有 3/7/30 日均量，每天 410 只。用来算「今天的期权量是平时几倍」 |
| `uw_oi_change_daily` | 逐个合约的未平仓量变化，每天 410 只、每只 50 个合约。**区分「新建仓」和「平仓了结」的唯一数据**——成交量大可能只是有人跑路，未平仓涨了才是真有人押新的。`oi_change` 是**倍数**（2.99 = 涨到原来的 2.99 倍） |
| `uw_market_ibs_daily` | 全市场内部人买卖的每日汇总，一天一行、`payload` 里带 500 天历史。`sells_notional` 是**负数**，比大小前先取绝对值 |
| `uw_gex_daily` | Options gamma walls (call/put wall, gamma flip/magnet). Coverage jumped from ~41 tickers to **~218** on 2026-09-02, when `tt-data-hub` stopped passing a Beijing-local `date` to the UW endpoint (measured 2026-09-03: 41/day through 09-01, 218 on 09-02). Still nowhere near the whole market |
| `user_watchlist` | User's tracked tickers. Time column is **`created_at`** (not `added_at`); `user_id` is a NOT NULL uuid; unique on `(ticker)`. **Written by this repo** |
| `user_positions` | `ticker`, `market`, `avg_cost`, `shares`, `currency`, `updated_at`. PK is `ticker` alone. **Written by this repo** |
| `analysis_history` | `id`, `created_at`, `skill`, `tickers` (comma-joined), `ticker_count`, `context_json`, `analysis_text`, `model`. Owned and **written by this repo** — see `sql/001_analysis_history.sql` |

Three tables that the old DuckDB had and Postgres deliberately does **not**:
`ai_models`, `skill_prompts`, `app_settings`. They are now `config/models.json`,
`config/skill_prompts.json`, and (for language) a plain env var. Do not re-create them.

### UW 实时接口的四个坑

1. **必须带浏览器 User-Agent**，否则 Cloudflare 直接 403（Error 1010）。
   跟 token 无关，换任何鉴权写法都救不了。
2. **`/api/alerts` 的 `symbol` 是期权合约代码**（`NVDA261002P00190000`），
   不是股票代码，标的在 `meta.underlying_symbol` 里。
3. **指数代码喂给 `/api/stock/...` 一律 422**（`^VIX` 拉小时线、拉伽马墙都是
   这个下场）。`core/uw.py` 在发请求之前就挡掉 `^` 开头的代码——明知要失败还
   去等十几秒只是白白拖慢工具。`tt-data-hub` 2026-09-03 也把它挡出了活跃池。
4. **`/api/stock/{票}/ohlc/1h` 的窗口是档位制**（1M/3M/6M/YTD/1Y/…），
   没有「刚好 N 根」这一档，而且**响应里混着盘前盘后**——`market_time` 分
   `r`（盘中）/ `pr`（盘前）/ `po`（盘后），只留 `r` 才跟库里的口径一致。
   实测 AAPL：1M 档只有 138 根盘中线（撑不起 200 小时均线），3M 档 390 根；
   两档耗时几乎一样（12.9 秒 vs 14.0 秒，开销全在代理那头），所以固定用 3M。

`.env` 里的键名是 `UW_T_ENDPOINT` / `UW_T_KEY`（历史遗留），
`config.py` 两个名字都认。

**测试里默认打不通 UW。** `tests/conftest.py` 把 `core.uw._fetch` 换成了抛异常的
桩（跟 AKShare 同一个待遇）——不拦的话，配了真密钥的机器上每个用例都会静悄悄
发真实请求，拿回真行情把断言打红。真要联网冒烟的用例加一个 `live_uw` fixture。

### A 股缺个股 → 行业的映射

`cn_universe.industry` 和 `stocks_meta.industry` 对 A股全是 NULL。
唯一带行业名的是 `cn_limit_pool_daily.industry`，只覆盖当日涨停的票。
所以板块强度只能给全市场排名，给不了「这只票所在的板块」。
补这个映射要在 tt-data-hub 做（把行业成分表灌进 `cn_universe.industry`），
不在本仓库。

### `uw_news.tickers` 是 jsonb 不是 text[]

匹配要用 `jsonb_exists_any(tickers, %s::text[])`。用 `&&` 会报
`operator does not exist: jsonb && text[]`。
（`news.related_tickers` 才是 text[]，那个用 `&&`。）

---

## MCP Protocol

Built with the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). Tools use `@mcp.tool()` decorators. Transport: `stdio` (default for Claude.ai desktop/CLI).

```python
# server.py pattern
from mcp.server.fastmcp import FastMCP
from tools.screening import screen_stocks
from tools.watchlist import list_watchlist, add_to_watchlist, remove_from_watchlist
from tools.positions import list_positions, add_position, remove_position
from tools.review_positions import review_positions
from tools.scan_swing import scan_swing
from tools.analyze_ticker import analyze_ticker
from tools.detect_catalysts import detect_catalysts
from tools.watch_positions import watch_positions

mcp = FastMCP("tt-ai-screener")
mcp.tool()(review_positions)
mcp.tool()(scan_swing)
# ... register all 12 tools

if __name__ == "__main__":
    mcp.run()
```

---

## Claude.ai Integration

### Remote (Claude.ai web — recommended)

```bash
python main.py
# Prints:
#   Web dashboard:  http://localhost:8766
#   MCP endpoint:   http://localhost:8766/mcp?token=<auto-generated>
```

In Claude.ai → Settings → MCP → Add custom integration, paste the MCP endpoint URL.
Token auto-generates on first run and saves to `.env`.

**公网接入只能开 `/mcp` 一个路径。** 走隧道（本项目用 Cloudflare Tunnel，
`tt.smartled.cc` → `localhost:8766`）时，隧道规则必须写 `path: ^/mcp`，并在
紧后面补一条同域名的 `http_status:404`。原因是网页面板跟 MCP 共用这个端口，
而面板的 25 个路由**一个都没有鉴权**——`/api/mcp-url` 会把 MCP 令牌明文交给
任何访问者，令牌校验就此失效；`/api/positions` 读持仓，`/api/positions/add`
和 `/analyze` 还能改数据、烧 AI 额度。2026-09-02 第一次接公网时真的这么漏了，
暴露约 40 分钟，之后轮换了令牌。网页面板留在局域网访问；要在外面看就用
Cloudflare Access 加登录，不要放开隧道。域名还要加进 `MCP_ALLOWED_HOSTS`，
否则 Host 白名单回 421。

### Local (Claude Desktop)

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "trading": {
      "command": "python",
      "args": ["/path/to/tt-trading-mcp/main.py", "--mcp"],
      "env": {
        "PG_READ_URL": "postgresql://readonly_user:...@host:5432/postgres",
        "PG_WRITE_URL": "postgresql://write_user:...@host:5432/postgres"
      }
    }
  }
}
```

---

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit with your API keys
```

```bash
# Unified entry point
python main.py              # Web + MCP remote on one port (default :8766)
python main.py --mcp        # MCP stdio only (for Claude Desktop)
python main.py --port 9000  # Override the port

# Tests (reads hit the real Postgres through the read-only account)
.venv/Scripts/python.exe -m pytest tests/ -q

# MCP inspector (debug tool calls)
npx @modelcontextprotocol/inspector python server.py
```

There is no `--update` and no `--cron`: data collection and its schedule belong to
`tt-data-hub` on the server. Anything that needs new data is fixed there, not here.
