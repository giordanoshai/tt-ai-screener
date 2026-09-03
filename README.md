# TT AI Screener

自建的选股 / 持仓分析 MCP 服务器（MCP = Model Context Protocol，Claude 用来调用外部工具的协议）。
接到 Claude.ai 上，用聊天的方式查自己的股票库。**Claude 就是界面**，网页面板只是个顺手能翻一翻的补充。

内置四种分析：

- **长线选股** —— 高增长、高毛利、站上 200 日均线的票
- **波段选股** —— 突破 / 回踩，带成交量确认
- **持仓复盘** —— 技术面 + 基本面 + 新闻 + 盈亏
- **自选池批量分析**

---

## 一、数据在哪，谁写的

这是理解整个项目最重要的一件事：**本项目一行行情数据都不写**。

```
  tt-data-hub  ──写──►   自建 Supabase (PostgreSQL)   ◄──读──  tt-trading-mcp  ──►  Claude.ai
（采集端，另一个仓库，      行情 / 基本面 / 新闻 / 期权             （本项目）           网页面板
 跑在服务器的 cron 上）                 ▲
                                        └──写── 只有自选、持仓、分析历史三张表
```

三条规矩，记住就不会出错：

1. **采集、建表、加字段、数据迁移，全部属于 `tt-data-hub`。** 这边缺数据就去那个仓库修，
   不要在这里加抓取代码。所以这里没有 `--update` 参数，也没有「更新数据」按钮 ——
   那两个是删掉的，不是留个空壳，因为一个只会道歉的按钮比没有还糟。想知道上次采集跑得
   怎么样，看 `ingest_runlog` 表（首页和 `GET /api/update/last` 都读它）。
2. **没有本地数据库文件。** `trading.duckdb` 是旧时代的遗物，没有任何代码读它。
3. **美股和 A 股装在同一批表里**，靠 `market` 列区分（`US` / `CN`）。A 股代码带交易所后缀
   （`600519.SH` / `000001.SZ` / `430139.BJ`）。查任何行情表都必须带 `market` 条件，
   美元和人民币的金额**绝对不能加在一起**。

### 只读的表（`tt-data-hub` 写入，本项目用只读账号访问）

规模是 2026-08-29 的实际数字，之后会一直长。

| 表 | 规模 | 装什么 | 坑在哪 |
|---|---|---|---|
| `stocks_meta` | 3.5 万行（美股 2.9 万 / A 股 5550） | 代码、公司名、行业、板块、交易所、`market`、`currency` | `sector` 只覆盖约 2000 只美股，A 股一只都没有，按板块筛的时候要考虑落空 |
| `stock_ohlcv_daily` | 380 万行 / 2.0 GB（美股 275 万，2021-02 起；A 股 105 万，2023-09 起） | 日线 OHLCV + RSI14 + 20/50/200 日均线 + 距均线百分比 + 20 日新高 + 量比 + ATR 百分比 | 主键 `(ticker, date)`，带 `market` 列 |
| `stock_ohlcv_min` | 2000 万行 / 7.9 GB | **小时线和分钟线挤在同一张表**，用 `timeframe` 列分（`'1h'` / `'1m'`） | 查询漏掉 `timeframe = '1h'` 就会撞上 2000 万行分钟线，是灾难。`ts` 是这根 K 线的**开始**时间，`timestamptz`，显示前要转 `America/New_York` 或 `Asia/Shanghai`。美股行还带 `session`（`RTH` 盘中 / `ETH` 盘前盘后） |
| `stock_fundamentals` | 2.9 万行 | 估值、增长、利润率、分析师评级、财报日、内部人交易情绪 (MSPR)、做空比例 | **一只票有很多行**（主键是 `(ticker, updated_at)`），必须用 `distinct on (ticker) ... order by ticker, updated_at desc` 去重。目前**只有美股**，A 股拿到空的是正常的 |
| `news` | 8.0 万行 | 新闻正文 + AI 打的情绪标签和分数 | **没有 `ticker` 列**，相关代码在 `related_tickers`（`text[]`，建了 GIN 索引，用 `&&` 匹配）；**没有 `headline` 列**，标题在 `title` |
| `uw_gex_daily` | 425 行 | 期权 gamma 墙（看涨墙 / 看跌墙 / gamma 翻转点 / 磁吸位） | 2026-09-02 起约 220 只（此前只有 41 只，上游修掉了日期传参的错） |
| `ticker_over20b` | 2130 行 | 市值 20 亿美元以上的股票池 | 板块信息缺失时当兜底数据源用 |
| `ingest_runlog` | — | 采集端每次跑批的结果 | 判断数据新鲜度用 |
| `v_ohlcv_hourly` | 视图 | `stock_ohlcv_min` 里 `'1h'` 那部分 | `tools/hourly.py` 故意不用它 —— 视图把 `session` 和 `timeframe` 藏起来了，而 Postgres 反正会把它展开成一模一样的执行计划 |

### 本项目写入的表（只有这三张）

| 表 | 装什么 | 关键点 |
|---|---|---|
| `user_watchlist` | 自选池 | 时间列叫 **`created_at`**（不是 `added_at`）；`user_id` 是非空 uuid；`ticker` 唯一 |
| `user_positions` | 持仓 | `ticker` / `market` / `avg_cost` / `shares` / `currency` / `updated_at`。**主键只有 `ticker`**，所以同一个代码不能在两个市场各持一份 |
| `analysis_history` | 每一次 AI 分析的存档 | 建表脚本在 `sql/001_analysis_history.sql`。**写失败绝不能让分析失败** —— 答案早就算完发给用户了，存档失败只记日志然后咽下去 |

除这三张之外的一切都走只读账号，而且那个连接还额外设成了只读事务：就算代码里
不小心写了条 UPDATE，数据库自己也会拒绝。

### 不在数据库里的配置

| 东西 | 在哪 | 谁改 |
|---|---|---|
| AI 模型清单 | `config/models.json` | 手改文件。设置页上是**只读展示** |
| 那些模型的密钥 | `.env` | 手改。**JSON 里只写环境变量名，从不写密钥本身** |
| 自定义 skill 提示词 | `config/skill_prompts.json` | 设置页的「保存」写它、「恢复默认」从里面删条目 |
| 默认 skill 提示词 | `skills/<语言>/<skill>.md` | 手改文件 |
| 输出语言 | `.env` 里的 `LANG_UI`（`en` / `cn`） | 手改。**不能在运行时切换**，设置页上没有这个开关 |

老版本里这几样存在数据库的 `ai_models` / `skill_prompts` / `app_settings` 三张表。
线上 Postgres **没有**这三张表，也不要再建 —— 配置迁到文件之后能进 git、能 review，
改完文件立刻生效（每次调用都重新读），不用重启。

### 写查询必须守的五条规矩

每一条都真的把这个项目搞崩过一次：

1. **占位符是 `%s`，不是 `?`。** 底层是 psycopg2 不是 DuckDB，留个 `?` 会直接报
   `syntax error at or near "?"`。
2. **任何行情表的查询都要带 `market` 条件。** 漏了不会报错，只会悄悄把 A 股混进美股结果里。
3. **别用相关子查询取最新交易日。** 写成
   `o.date = (select max(date) from stock_ohlcv_daily where ticker = o.ticker)`
   实测是 275 万行全表扫、6.7 秒。只查几只票就按票 `order by date desc limit 1`
   （顺带能保住停牌、退市的票）；全池扫描就先单独查一次这个市场的最新交易日，
   再拿去做等值比较（约 109 毫秒）。
4. **`stock_fundamentals` 一只票有很多行**，必须 `distinct on (ticker)` 去重。
5. **`stock_ohlcv_min` 一张表混了三种周期**（`1h`/`1m`/`5m`，靠 `timeframe`
   列区分）。分钟级数据约 2000 万行、9.3GB，漏了 `timeframe` 条件是灾难。

---

## 二、环境要求

- Python 3.10+
- 一个能连的 PostgreSQL（自建 Supabase 即可），并且已经由 `tt-data-hub` 灌好数据
- 数据库上两个账号：`readonly_user`（读一切）和 `write_user`（只写上面那三张表）
- Claude.ai 账号（用远程 MCP 需要 Pro 及以上）
- 可选：一个 OpenAI 兼容的大模型接口，用来跑 skills 分析

---

## 三、快速开始

```bash
git clone https://github.com/giordanoshai/tt-ai-screener.git
cd tt-ai-screener

python -m venv .venv
source .venv/bin/activate        # Mac / Linux
# .venv\Scripts\activate         # Windows
pip install -r requirements.txt

cp .env.example .env             # 至少要填 PG_READ_URL 和 PG_WRITE_URL
python main.py
```

启动后会打印：

```
Web dashboard:  http://localhost:8766
MCP endpoint:   http://localhost:8766/mcp?token=<首次运行自动生成>
```

命令行只有三个参数：

```bash
python main.py              # 网页 + 远程 MCP 跑在同一个端口（默认 8766）
python main.py --mcp        # 只跑 MCP stdio（给 Claude Desktop 用）
python main.py --port 9000  # 换端口
```

跑测试（读取会真的连线上只读账号）：

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

---

## 四、接到 Claude.ai

### 远程（Claude.ai 网页版，推荐）

`python main.py` 起来之后，在 Claude.ai → 设置 → 连接器 → 添加自定义连接器，
把上面那个 MCP endpoint 地址粘进去。token 首次运行自动生成并写回 `.env`。

局域网内直接用 `http://<服务器 IP>:8766/mcp?token=...` 就行。但 Claude.ai 网页版
是从公网发请求的，够不着局域网地址，要架一条隧道。本项目实际用的是 Cloudflare
Tunnel：`tt.smartled.cc` → `localhost:8766`。

#### ⚠️ 放公网时，隧道只能开 `/mcp` 这一个路径

**网页面板的每一个路由都没有鉴权**，而它和 MCP 共用同一个端口。隧道直接指向
8766 的话，等于把下面这些对全网敞开：

| 路径 | 后果 |
|---|---|
| `/api/mcp-url` | **把 MCP 令牌明文交出去** —— 拿到就能调用全部 12 个工具 |
| `/api/positions` | 持仓明细（成本、股数）|
| `/analyze/history` | 全部历史分析记录 |
| `/api/positions/add`、`/ticker/remove` | 改你的持仓 |
| `/analyze` | 触发 AI 分析，烧你的 API 额度 |

第一条尤其致命：它让 MCP 那层 token 校验**完全失效**，等于门锁挂在门外的钉子上。
2026-09-02 第一次接公网时就是这么漏的，暴露约 40 分钟。

所以隧道配置必须限定路径，同域名的其余请求一律 404：

```yaml
  - hostname: tt.smartled.cc
    path: ^/mcp
    service: http://localhost:8766
  # 这条必须【紧跟】在上面那条后面：ingress 从上往下第一个匹配就生效
  - hostname: tt.smartled.cc
    service: http_status:404
```

改完先自测再重启，别直接重启——配置写错会把同一条隧道上的其它域名一起带挂：

```bash
cloudflared --config <文件> tunnel ingress validate          # 语法
cloudflared --config <文件> tunnel ingress rule <某个 URL>   # 逐条看命中哪条规则
```

网页面板就留在局域网访问（`http://192.168.1.38:8766`）。哪天想在外面看面板，
用 Cloudflare Access 加一层登录，**不要**直接放开这里。

另外记得把域名加进 `.env` 的 `MCP_ALLOWED_HOSTS`，否则 MCP SDK 自带的 Host
白名单会回 421。

### 本地（Claude Desktop）

改 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "trading": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/tt-ai-screener/main.py", "--mcp"],
      "env": {
        "PG_READ_URL": "postgresql://readonly_user:...@host:5432/postgres",
        "PG_WRITE_URL": "postgresql://write_user:...@host:5432/postgres"
      }
    }
  }
}
```

### 然后直接问就行

> 「今天有什么长线标的？」
>
> 「有没有波段机会？只看突破的」
>
> 「分析一下我的 NVDA 持仓，数据怎么说」
>
> 「把 AAPL、MSFT、GOOGL 加到自选」
>
> 「NVDA 现在这个位置，小时线上买点在哪」

---

## 五、MCP 工具（12 个）

五个场景工具（针对具体交易场景，工具内部算完直接返回结论，不调用大模型）：

| 工具 | 干什么 |
|---|---|
| `review_positions` | 我手上这几只票该不该动：盈亏、止损位、有没有跌破、财报临不临近、消息面、期权/资金流向，**按需要动手的紧急程度排序** |
| `scan_swing` | 盘后找值得做的波段机会：形态和理由、买入区间、按 ATR 算的止损、目标价。`scope=pool` 只看持仓和自选，`scope=full` 扫全市场 |
| `analyze_ticker` | 单独一只票的完整体检：日线趋势 + 小时线买卖点位（原 `get_hourly_setup` 并入这里）、基本面、消息面、期权资金流向 |
| `detect_catalysts` | 最近出了什么事，跟我的票有没有关系，股价反应过了没有 |
| `watch_positions` | 持仓上有没有需要立刻处理的信号，**没事就说没事**，不制造噪音 |

选股 + 自选 + 持仓的增删查（本轮没动）：

| 工具 | 干什么 |
|---|---|
| `screen_stocks` | 参数化选股。**至少要给一个筛选条件**，不给直接拒绝；结果最多 30 条 |
| `list_watchlist` | 列出自选池，带最新价、当日涨跌、技术指标 |
| `add_to_watchlist` | 加自选（这只票必须已经在 `stocks_meta` 里，否则拒绝） |
| `remove_from_watchlist` | 删自选 |
| `list_positions` | 列出持仓和盈亏。汇总是**按币种分开的**（`{"USD": {...}, "CNY": {...}}`），没有跨币种总计 |
| `add_position` | 加 / 改持仓，`market` 决定币种 |
| `remove_position` | 删持仓 |

下线了三个工具：`analyze_watchlist`（被 `scan_swing(scope="pool")` 取代）、
`analyze_positions`（被 `review_positions` 取代）、`get_hourly_setup`（并进
`analyze_ticker`，计算逻辑还在，只是不再单独注册成 MCP 工具）。

工具的设计原则：**所有筛选和汇总都在函数里做完**（能交给数据库就交给数据库），
只把结论丢给 Claude，绝不返回原始整表 —— 这是省 token 的关键。

---

## 六、网页面板

`http://localhost:8766`

| 页面 | 内容 |
|---|---|
| 首页 | 系统状态、数据新鲜度（读 `ingest_runlog`）、AI 模型概览 |
| 自选 | 增删自选，看价格 / RSI / 涨跌 |
| 选股 | 多维度筛选：板块 + 技术面 + 基本面 |
| 持仓 | 持仓和盈亏 |
| 历史 | 翻以前每一次 AI 分析的存档 |
| 设置 | AI 模型（只读展示）、skill 提示词编辑 |

---

## 七、目录结构

```
tt-ai-screener/
  main.py                # 统一入口（网页 + MCP，或者纯 MCP stdio）
  server.py              # MCP 服务器，注册 12 个工具 + token 鉴权
  config.py              # Postgres 地址、AI 兜底配置、MCP 端口和 token
  ai_client.py           # OpenAI / Anthropic 两种格式的 AI 客户端，读 config/models.json
  config/
    models.json          # AI 模型清单（不含任何密钥，只写环境变量名）
    skill_prompts.json   # 自定义提示词，没在设置页保存过就不存在这个文件
  sql/
    001_analysis_history.sql   # 这个仓库唯一拥有的一张表
  db/
    pg.py                # 唯一的数据库出入口（读写分账号）
  core/
    snapshot.py / scoring.py / catalyst.py / fundamental.py / regime.py /
    cn_signals.py / uw.py   # 五个场景工具共用的取数层，纯 Python + SQL
  tools/
    screening.py         # screen_stocks
    watchlist.py         # 自选的增删查
    positions.py         # 持仓的增删查
    hourly.py            # 小时线计算逻辑，被 analyze_ticker 调用，不再单独暴露为 MCP 工具
    review_positions.py / scan_swing.py / analyze_ticker.py /
    detect_catalysts.py / watch_positions.py   # 五个场景工具
  skills/
    __init__.py          # 网页面板分析页的取数层（MCP 工具已经不用它了）
    en/, cn/             # 两种语言的提示词：
                         #   longterm_screen.md / swing_screen.md / position_review.md
  web/
    app.py               # FastAPI 网页面板
    templates/           # 首页 / 自选 / 选股 / 分析 / 历史 / 设置
  tests/
  requirements.txt
```

`db/pg.py` 是唯一的数据库出入口，接口就四个：
`pg.query(sql, params)` 返回 list[dict]、`pg.query_one(...)` 返回 dict 或 None、
`pg.execute_write(...)` 返回影响行数、`pg.fetch_write(...)` 给带 `RETURNING` 的写入用。
Postgres 的 `numeric` 在这一层就转成 `float`（`Decimal` 会让 `json.dumps` 直接炸）。

---

## 八、隐私

所有数据都在你自己的 PostgreSQL 里。对外的网络请求只有两类：你自己配的 AI 接口，
以及 Claude.ai 那条 MCP 连接。持仓和交易记录不会离开你自己的机器和数据库。

`config/models.json` 会进 git，所以它里面**永远不放密钥**，只放环境变量的名字；
真正的密钥在 `.env`，而 `.env` 在 `.gitignore` 里。

---

## License

MIT
