# Changelog

All notable changes to this project will be documented in this file.

### [2026-08-29] 改造为只读 Postgres 消费端（计划 2 的任务 1~5）

数据采集已全部移交独立的 `tt-data-hub` 服务，本项目退化成只读消费端
（自选和持仓的增删改除外）。五个任务由五个实施者并行完成，全部走
「先写红测试 → 改代码 → 变绿」，并做了变异测试（故意改坏实现，验证测试
真的咬得住）。新增 211 个测试，全绿。

* **新增 (Added)**: `db/pg.py` —— 唯一的数据库出入口。读走 `readonly_user`
  且连接额外设为只读事务（第二道防误写闸，实测报错从「没有权限」变成
  「只读事务里不能执行 INSERT」），写走 `write_user`。把 Postgres 的
  `numeric` 统一转成 `float`：`stock_fundamentals` 有 20 多个 numeric 列，
  psycopg2 默认给出 `Decimal`，而 `Decimal` 不能直接转 JSON，不在访问层
  处理的话上面每个工具都要各踩一遍。
* **新增 (Added)**: `tools/hourly.py` 的 `get_hourly_setup` —— 小时线买点
  工具。返回约 1.5 KB 的纯标量摘要，不含任何原始 K 线（有测试钉住：不许
  出现 dict 数组、JSON 必须小于 3000 字符）。覆盖做波段真正要问的四件事：
  现价卡在区间哪个位置、均线方向（含斜率，比「站没站上」更说明问题）、
  量能几倍（均量刻意排除最后一根，否则放量会被自己稀释）、每根平均波动
  多少钱（止损别放得比这更近）。支撑阻力用摆动高低点算，不是简单的区间
  最高最低——那才是市场真正掉过头的位置。
* **修改 (Changed)**: `tools/watchlist.py` / `positions.py` / `screening.py`
  全部改连 Postgres。占位符从 DuckDB 的 `?` 换成 `%s`；每条查询都带上
  `market` 条件（日线表现在混了美股和 A股）。
* **修改 (Changed)**: 持仓改为**分币种**汇总——美股计美元、A股计人民币，
  汇总里根本不存在跨币种的总数。
* **修改 (Changed)**: `screen_stocks` 新增 `market` 参数；行业分类暂时从
  `ticker_over20b` 兜底取（最新交易日 2082 只可筛票里 2074 只能取到，
  而 `stocks_meta.sector` 目前全为空），每次返回都带 `sector_source`
  字段明说这是临时兜底；板块名做了归一化和同义词映射（「Health Care」
  和「Healthcare」自动相等）。
* **修复 (Fixed)**: 选股查询的性能。实测对比四种取最新交易日的写法
  （日线表 275 万行）：原来的相关子查询 738 万代价 / 6750 毫秒；计划推荐的
  `group by market` 公共表达式**同样是全表扫**，18 万代价 / 375 毫秒；
  按票 LATERAL 倒扫 13 万 / 203 毫秒；最终选的两段式（先单查最新日再当
  常量传进主查询）**5541 代价 / 109 毫秒**。性能守卫盯的是「计划内最大
  节点代价」而不是顶层——顶层有 `limit` 兜着会漏掉底下藏的全表扫。
* **修复 (Fixed)**: 三处按真库实测纠正的移植错误 ——
  ① `user_watchlist` 的冲突键是 `(ticker)` 单列不是 `(user_id, ticker)`，
  按后者写会直接抛唯一键冲突；② `user_positions` 主键只有 `ticker` 不含
  `market`，`ON CONFLICT (ticker, market)` 会报「没有匹配的唯一约束」；
  ③ 时间列叫 `created_at` / `updated_at`，不是 DuckDB 那版的 `added_at`。
* **修改 (Changed)**: `config.py` 改用 `PG_READ_URL` / `PG_WRITE_URL`
  （各保留一次对旧键名 `READ` / `WRITE` 的兼容读取），新增
  `WATCHLIST_USER_ID`；删除全部采集相关配置（`DB_PATH`、`FINNHUB_*`、
  `NEWS_*`、`OHLCV_BATCH_SIZE`、`FUNDAMENTALS_STALE_DAYS`、`TICKERS_FILE`、
  `load_tickers`）。
* **移除 (Removed)**: `analyze_positions` 里那段走 `data_sources/uw_client.py`
  的调用。那个客户端从来没调通过——它写死了 `https://api.unusualwhales.com/api`，
  读的配置项 `UW_API_TOKEN` 在 `.env` 里根本不存在。文件本身没删，留给任务 6。

**已知未完成**：任务 6（删 DuckDB 与采集代码）、任务 7（部署与 Cloudflare
Tunnel）尚未开始；另外 `web/app.py` 里有一套跟 MCP 工具完全平行的 DuckDB
实现（20 处 `get_conn()`、`/screen` 端点自己拼 SQL 且不带 market 条件、
持仓端点没有币种），任务 6 一删 `db/init.py` 它就会 import 失败，需要单独
排一个任务。


### [2026-08-28] 统一数据层实施计划（两份）
* **新增 (Added)**: [`docs/superpowers/plans/2026-08-28-tt-data-hub.md`](file:///d:/Dev_project/Python_Project/tt-trading-mcp/docs/superpowers/plans/2026-08-28-tt-data-hub.md) —— 新建统一采集服务的 10 个任务，含完整代码、单元测试、试跑与验收命令。覆盖：仓库骨架与代码格式转换、数据库统一写入层与运行日志、技术指标移植（附与旧实现对拍的步骤）、yfinance 日线采集（逐只重试补齐现在每天漏的 110 只）、拆股断层检测与修复、UW 客户端与活跃池与美股小时线、A股股票池与日线（含除权漂移检测）、A股小时线与行业分类补齐、UW 快照迁移与基本面、部署与定时任务切换。
* **新增 (Added)**: [`docs/superpowers/plans/2026-08-28-tt-trading-mcp-postgres.md`](file:///d:/Dev_project/Python_Project/tt-trading-mcp/docs/superpowers/plans/2026-08-28-tt-trading-mcp-postgres.md) —— 本项目改造为只读消费端的 7 个任务：Postgres 读写分离访问层、自选/持仓/选股三个工具的移植、新增 `get_hourly_setup` 小时线买点工具、删除 DuckDB 与全部采集代码、systemd 部署与 Cloudflare Tunnel。
* **修复 (Fixed)**: 实测推翻了 spec 里两处设计并已更正 —— ① **UW 没有 `/splits` 端点（404），且 UW 的 K 线数据本身就不复权**（KLAC 在 UW 那边同样是 2411→254 的断崖），所以日线主源必须是 yfinance（实测它的历史是连续的 241→254），UW 改为只负责小时线与衍生数据；② 拆股修复方案改为「跳空体检 + yfinance 重拉历史 + 清空该票小时线」。
* **修改 (Changed)**: 补充 UW 接口的四个实测坑到 spec 4.2.1 —— 不带浏览器 User-Agent 会被 Cloudflare 拦（Error 1010）；K 线每个交易日返回盘前/盘中/盘后三行，只有 `market_time='r'` 是真的；`total_volume` 字段数值错误，只能用 `volume`；`limit`/`start_date` 参数无效，控制窗口的是 `timeframe`（1M/3M/6M/YTD/1Y/2Y/3Y/5Y，MAX 报 422）。
* **修改 (Changed)**: 发现三处 spec 之外的移植障碍并写入计划 —— 线上 `user_watchlist` 的时间列是 `created_at` 而非 `added_at` 且有非空 `user_id`；现有选股 SQL 的相关子查询在 Postgres 里是全表扫 274 万行（执行计划代价 722 万），须改写为 `latest` 公共表达式；`stock_fundamentals` 主键是 `(ticker, updated_at)`，连表须用 `distinct on` 取最新一条。
* **移除 (Removed)**: 删除已过时的 `PLAN.md` 与 `docs/supabase_uw_integration_architecture.md`（两者均声称 Supabase 在云端、`stocks_meta` 含行业分类，与实情不符）。
* **修复 (Fixed)**: 更正此前「UW 没有拆股端点」的错误结论 —— 端点存在，路径是 `/api/companies/{票}/splits`（此前误试 `/api/stock/{票}/splits` 才 404）。实测 KLAC 返回 `{"effective_date":"2026-06-12","split_factor":10.0}`，与库内断层完全吻合。拆股方案据此改为「日历判断 + yfinance 修复」两段式，弃用原先靠成交量比值猜测的启发式（降级为查不到日历时的兜底）。
* **新增 (Added)**: spec 5.5.1 给出「存本地 vs 用时再拉」的判断规则（命中「需跨股票批量筛选」「当天不采永久丢失」「每次分析都要用」任一条才存），并按此规则把 UW 全部数据分类归位：新增该存的三样（完整基本面、上市退市清单、空头比例），停掉国会交易两张表，内部人交易由每日降为周更，伽马墙从 466 行扩为活跃池每日全采；暗池、财报历史、盈利预期、季节性、机构持仓等一律不存，按需拉取。
* **新增 (Added)**: spec 5.5.2 —— 基本面改用 UW `/api/companies/{票}/profile`（51 字段）。**实测证实现有 `stock_fundamentals` 数据本身是准确的**（AAPL 的 `return_on_equity_ttm` 1.488、`quarterly_revenue_growth_yoy` 0.164 与库内完全一致，说明本就同源），更换理由是字段太少与更新太慢：字段由 18 扩至约 30，补上填充率仅 49% 的 `peg_ratio`、71% 的净利润增速，并新增前瞻市盈率、企业价值倍数、beta、52 周高低点、五档分析师人数分布等。
* **新增 (Added)**: spec 5.5.3 与 `us_universe` 任务 —— 用 `/api/companies/listings` 一次拉全量（活跃 14389 只、已退市 9449 只，含上市日与退市日），每周滚动收录新上市、标记已退市、写入上市日期。**这同时解决了每日漏采 110 只票的部分成因**：其中一些早已退市，采集程序仍在天天拉取并失败；`us_pool()` 相应加入 `delisted_at is null` 过滤。
* **修改 (Changed)**: 明确「小时线是最细的工作周期」这条边界 —— 本系统做波段、长线与持仓分析，不做日内交易，故分钟线定位为**只供手工复盘的归档数据，不建任何定时任务**。据此三处收紧：① 新增只覆盖小时线的部分索引 `idx_ohlcv_min_hourly`（小时线与 1994 万行分钟线同表，而现有唯一索引的 `timeframe` 排在第四列，按票查小时线会扫到大量分钟线行）；② 两个小时线任务的清理语句补上 `market` 条件，避免互删对方市场的数据；③ 验收清单新增第 7 条，用等值断言分钟线行数未被改动，防止哪条清理语句漏写 `timeframe = '1h'`。

### [2026-08-28] 统一数据层设计方案（美股 + A股）
* **新增 (Added)**: 设计文档 [`docs/superpowers/specs/2026-08-28-统一数据层设计.md`](file:///d:/Dev_project/Python_Project/tt-trading-mcp/docs/superpowers/specs/2026-08-28-%E7%BB%9F%E4%B8%80%E6%95%B0%E6%8D%AE%E5%B1%82%E8%AE%BE%E8%AE%A1.md)。核心内容：把散在四个项目里的采集合并成独立的命令行采集程序 `tt-data-hub`（唯一写库角色），`tt-trading-mcp` 退化为只读消费端；A股经 AKShare 并入现有表（`market='CN'`，代码格式 `600519.SH`）；新增小时线层，只采「持仓 + 自选 + 粗筛命中」的活跃池约 400 只，复用 `stock_ohlcv_min` 的 `timeframe='1h'`。含建表 SQL、任务清单、定时排期、14 步不断档切换顺序与验收清单。
* **新增 (Added)**: 实测确认 A股数据源可用性 —— 那台 AKShare 服务（AKTools 1.18.64）的**东方财富源全部返回 500**，方案改为只用新浪源：日线 `stock_zh_a_daily`、小时线 `stock_zh_a_minute`（单次 1970 根覆盖两年）、全市场快照 `stock_zh_a_spot`、市值 `stock_zh_a_gdhs`、财务 `stock_financial_abstract`。
* **修复 (Fixed)**: 定位到一个**现存数据故障**并写入修复方案 —— `stock_ohlcv_daily` 只写最新一天、从不回刷历史，导致拆股后新旧价格断裂。库内 2026 年有 115 处，KLAC（1拆10）、CRWD（1拆4）、MNST（1拆2）、MQ（4合1）等大票的均线、20/55日新高、ATR 目前全部失真。方案新增 `us_splits` 任务（UW 拆股日历为主 + 全库跳空体检兜底）并要求上线前做一次全量修复。
* **修改 (Changed)**: 纠正上一版架构文档的两处事实偏差 —— ① Supabase 是自建在局域网 `192.168.1.38`，不在云端，故 MCP 部署需配 Cloudflare Tunnel；② `stocks_meta` 实际只有 7 列，**没有 sector/industry/market**，`screen_stocks` 的板块筛选参数迁移后会直接失效，方案增加 `meta_refresh` 任务补齐。

### [2026-08-28] Supabase (PostgreSQL) 资产梳理与 Unusual Whales 全量融合架构落地
* **新增 (Added)**: 创建架构设计文档 [`docs/supabase_uw_integration_architecture.md`](file:///d:/Dev_project/Python_Project/tt-trading-mcp/docs/supabase_uw_integration_architecture.md)，系统梳理 Supabase 中 9 张核心资产表（274 万行技术面行情、3 万条基本面、8000+ 条 UW 结构化新闻流、2500 笔真实交易记录），并设计由单机 DuckDB 往 Supabase (PostgreSQL) 中央数据库的平滑迁移与融合蓝图。
* **新增 (Added)**: 创建并更新项目推进计划 [`PLAN.md`](file:///d:/Dev_project/Python_Project/tt-trading-mcp/PLAN.md)，详细拆解 API 调研、K 线深度验证、数据层迁移、增量更新与多维量化分析的实施步骤。
* **修改 (Changed)**: 完成 Unusual Whales API (adv KEY) 的实测与深度校验：确认 `1h`（覆盖 7+ 个月）、`30m`（覆盖 4 个月）、`15m` 及 `1m/5m` 的单次 2500 根 K 棒拉取能力与历史回溯参数支持，确立“大周期日线定方向 + 小周期1h/30m找买点与风控”的双周期架构。

### [2026-07-16] 阿里云百炼 (Bailian) 异步新闻情绪批推理改造
* **新增 (Added)**: `main.py` — 新增后台 `sentiment-harvester` 守护线程。当启动 Web 容器时，该线程会在后台每隔 **5分钟** 自动醒来，调用百炼 API 查询是否有完成的批量新闻打分任务，若有则自动下载并悄悄写回数据库。这保证了在服务运行期间，处理完的情绪能够被完全自动导入，实现全自动无人值守。
* **修改 (Changed)**: `db/update.py` — 重构 `update_news_sentiment()` 调度流程。从同步阻塞、多线程高频调用，改造成**非阻塞、异步分步轮询**架构。每次更新时优先检测本地是否存在活跃的 Batch ID，若百炼平台已运行完毕（status=completed）则直接下载 JSONL 并回写入库，同时删除活跃 ID；若没有活跃 ID 则筛选最新未评分新闻上传（每批限 1000 条内）并创建新的 Batch 任务存入 settings 表。
* **修改 (Changed)**: `ai_client.py` — 新增 `submit_news_sentiment_batch()`、`check_batch_status()` 与 `download_batch_results()` 接口函数，以符合百炼/OpenAI兼容 Batch 推理的 API 标准。
* **修改 (Changed)**: `ai_client.py` — 增加百炼批处理机型自动重映射。由于百炼 Batch 模式暂不支持 `deepseek-v4-flash` 模型，提交至批推理任务时模型会自动重映射/平滑降级到官方完全支持的 `qwen-plus` 运行。
* **修改 (Changed)**: `ai_client.py` — `get_model` 添加 `api_key` 的环境变量 fallback。若数据库中密钥为空（在 Web 界面尚未配置），程序会自动回退到本地 `.env` 中的 `SENTIMENT_API_KEY`/`AI_API_KEY`，解决 CLI 环境因授权失败导致的 401 错误。

### [2026-07-16] Finnhub API 速率优化 — Fundamentals 更新提速约 4 倍
* **修改 (Changed)**: `data_sources/us_market.py` — 新增 `FinnhubRateLimiter` 线程安全单例限速器，替代每次调用后固定 `time.sleep(1.1)` 的粗放策略。限速器在请求前 throttle，自动扣除 HTTP 请求耗时，Finnhub 配额利用率从 ~45% 提升至 ~90%（实际吞吐量翻倍）。
* **修改 (Changed)**: `data_sources/us_market.py` — `fetch_fundamentals()` 新增 `light=True` 模式：仅拉 metrics+profile（2 次 API），跳过 earnings calendar 和 insider transactions（省 2 次 API），适用于 universe tier 的非核心 ticker。
* **修改 (Changed)**: `data_sources/base.py` — `FundamentalsProvider` 抽象接口新增 `light` 参数。
* **修改 (Changed)**: `db/update.py` — `update_fundamentals()` 改为接收 `tickers_with_tier`（含 tier 信息），core ticker 使用完整 4 调用，universe ticker 使用 light 2 调用。预计 2169 ticker 全量更新从 ~5 小时降至 ~1.2 小时。

### [2026-07-16] UW API 集成代码审查修复
* **修复 (Fixed)**: `config.py` — UW API Token 不再硬编码为默认值，改为空字符串，防止源码泄露密钥。`.env` 中变量名从 `UW_API_KEY` 修正为 `UW_API_TOKEN`，与 config 读取一致。
* **修复 (Fixed)**: `skills/__init__.py` — `gather_context("position_review")` 中 UW Smart Money 数据（`uw_*` 前缀字段）被丢弃的 bug，现已正确合并到 LLM 上下文中。
* **修复 (Fixed)**: `tools/screening.py` + `tools/positions.py` — 未配置 `UW_API_TOKEN` 时，跳过 UW API 调用，避免 100 次无效 401 请求拖慢响应。
* **修改 (Changed)**: `data_sources/uw_client.py` — 去掉 URL query param 重复传递 token 的安全风险；`get_comprehensive_profile` 改为 `ThreadPoolExecutor` 并行调用 10 个端点（5 线程），单 ticker 从 ~2s 降至 ~0.5s。
* **修改 (Changed)**: `tools/screening.py` — Insider 数据从无意义的 `len(data[:5])` 改为区分买/卖计数（`uw_insider_buys` / `uw_insider_sells`）；`import datetime` 移到文件顶部。
* **修改 (Changed)**: `tools/positions.py` — 复用 `_extract_uw_summary()` 提取函数，消除 screening/positions 间的重复代码。
* **新增 (Added)**: `data_sources/uw_client.py` 新增 `uw_available()` 便捷方法，用于检查 UW Token 是否已配置。
* **修改 (Changed)**: `.env.example` 新增 `UW_API_TOKEN` 配置项说明。

### [2026-07-16] 接入 Unusual Whales API (Smart Money 策略)
* **新增 (Added)**: 创建 `data_sources/uw_client.py`，专门为**长线持仓**和**短线波段 (Swing)** 接入了 10 个 Unusual Whales 接口：内部人交易、国会议员持仓、机构持仓趋势、真实做空比例、FTDs、暗盘交易、月度季节性规律、波动率异常得分、期权多空立场等。
* **修改 (Changed)**: 更新 `tools/screening.py`。在 DuckDB 基础筛选后，对排名前 10 的候选股票自动拉取全套 UW "Smart Money" 和 "Options Flow" 深度数据，极大增强 LLM 的选股判断力。
* **修改 (Changed)**: 更新 `tools/positions.py` (`analyze_positions`)。分析当前持仓时，将深度整合机构资金与空头数据，帮助捕捉波段见顶或长线建仓信号。
* **修改 (Changed)**: `config.py` 中增加 `UW_API_TOKEN` 环境变量配置。

### [2026-07-13] 新增项目英文介绍文档
* **新增 (Added)**: 在 `docs/introduction_en.md` 新增极简版项目英文介绍文档（控制在600字符以内），阐述了项目的核心卖点、设计理念（"LLM as the UI"）和核心功能。

### [2026-06-23] 项目重命名 TT AI Screener + 移除 setup.py + 重写 README
* **修改 (Changed)**: 项目从 `tt-trading-mcp` 重命名为 **TT AI Screener**（`tt-ai-screener`），更新 server.py、web/app.py、main.py、manage.sh、run_daily_update.bat、settings.html 中所有引用。
* **修改 (Changed)**: 删除过时的 `setup.py` 首次安装向导。`main.py` 启动时已自动初始化数据库，数据更新用 `--update`，ticker 管理已迁移到 watchlist 工具。
* **修改 (Changed)**: 重写 `README.md`，反映当前架构：`main.py` 统一入口、Remote MCP 连接、Web Dashboard、AI 配置、完整工具列表和项目结构。
* **修改 (Changed)**: `.env.example` AI 配置改为注释（可选），注明推荐通过 Web Settings 页面配置，`.env` 仅作首次 seed。

### [2026-06-23] 品牌重命名 + Home 首页 + 共享导航 + 定时更新 + News 重试
* **修改 (Changed)**: 全站品牌从 `tt-trading-mcp` 重命名为 **TT AI Screener**，统一所有页面 title 和 header
* **新增 (Added)**: `header.html` 共享导航模板 — 所有 6 个页面 include 同一份 nav，通过 `active_nav` 变量高亮当前页，右侧区域各页面自定义
* **新增 (Added)**: `/` 路由改为 Home 首页 — 紧凑双栏一页布局：左侧更新摘要 + MCP 连接（带 Copy Prompt 一键复制），右侧 Quick Start（含 AI 模型配置引导）
* **新增 (Added)**: Quick Start 新增 **Configure AI Models** 步骤 — 引导用户在 Settings 配置 News Sentiment 和 Stock Analysis 两个 AI 模型
* **新增 (Added)**: `GET /api/mcp-url` 端点 — 自动获取当前 host 的 MCP endpoint URL（含 token），Copy Prompt 包含完整使用说明
* **新增 (Added)**: `update_history` 表 + `GET /api/update/last` — 持久化每次更新的摘要（各阶段 ticker 数、耗时、错误）
* **新增 (Added)**: Settings 页新增 Data Update Schedule — 开关 + 时间选择，支持运行时动态启停 cron 线程
* **新增 (Added)**: `GET/POST /settings/schedule` 端点 — 配置存入 `app_settings` 表
* **修改 (Changed)**: `main.py` 启动时自动读取 DB schedule 配置；支持 `apply_schedule()` 运行时切换
* **修改 (Changed)**: Watchlist 页面从 `/` 迁移到 `/watchlist`
* **修复 (Fixed)**: `update_news` 新增重试机制（max_retries=2, sleep 3s），SSL 断连的 ticker 可自动重试

### [2026-06-17] 重写 swing_screen skill（对齐 JSON 架构）
* **修复 (Fixed)**: 形态 B（均线回调买点）死逻辑 —— 内置筛选强制 `close>MA20`，原 `dist_ma20_pct` 负值区间永远命中不了。重定义为 [-0.02, 0.025] 浅回调，并注明负值仅在 screener 源出现。
* **修改 (Changed)**: 去掉 prompt 内全部 SQL，改为「系统投喂 JSON、AI 只做分析」的写法，与 Prompt Design Guide 的设计规则对齐（cn + en 两版同步）。
* **新增 (Added)**: swing 量化评分 rubric（形态/量能/动量/情绪/风险 共 100 分）+ A/B/C 评级阈值；财报风险与超买强制封顶 B。
* **新增 (Added)**: 止损/目标用 `atr_pct` 折算为具体价位（MA50 或 2×ATR 止损、3×ATR 或前高目标）；新增数据来源差异说明（built-in 已预筛 vs screener 未过滤需自检趋势）。
* **修改 (Changed)**: Prompt Design Guide（`settings.html`）的 Swing 段不再写「Same fields as Long-Term」，改为列出内置 swing 实际投喂的字段，并注明 built-in（仅技术字段）与 screener（含完整基本面、未预筛趋势）两种来源的差异。

### [2026-06-17] i18n + custom skill prompts + Prompt Design Guide
* **Added**: EN/CN dual-language skills (`skills/en/`, `skills/cn/`)
* **Added**: `skill_prompts` DB table — per-skill per-language custom prompt storage
* **Added**: Settings page: Skill Prompts section with inline editor, save/reset/view-default per skill
* **Added**: Prompt Design Guide — one-click copyable meta-prompt with full data schema, field descriptions, value ranges, JSON examples, and design rules; users paste to any AI to generate custom prompts
* **Added**: `GET/POST /settings/prompts`, `POST /settings/prompts/reset` API endpoints
* **Added**: `app_settings` DB table + `GET/POST /settings/lang` API for language preference
* **Added**: Language selector on Settings page (English / 中文 toggle)
* **Changed**: `load_skill_prompt()` priority: DB custom > file default, with `lang` param
* **Changed**: Analysis endpoints dynamically switch response language based on setting
* **Changed**: `.skills/*.skill` ZIP files rebuilt with English content for MCP/Claude Desktop

### [2026-06-17] Analysis 页面 + 持仓管理
* **新增 (Added)**: `/analysis` 页面 — 3 个 Skill 分析卡片（Long-Term / Swing / Position Review）
* **新增 (Added)**: 持仓管理表格 — 添加/删除持仓，自动从 DB 获取当前价、计算盈亏百分比和金额、市值汇总
* **新增 (Added)**: `user_positions` 表 — 存储用户持仓（ticker, avg_cost, shares）
* **新增 (Added)**: `GET /positions` 端点 — 返回持仓列表，含实时价格和 P&L 计算
* **新增 (Added)**: `POST /positions/add` / `POST /positions/remove` 端点
* **修改 (Changed)**: 三页导航栏统一为 Watchlist / Screener / Analysis

### [2026-06-17] 统一入口 main.py + 实时更新进度
* **新增 (Added)**: `main.py` — 统一入口，支持 `--web`（Web only）、`--mcp`（MCP only）、默认 Web+MCP 同时启动
* **新增 (Added)**: `--cron HH:MM` 参数 — 每日定时自动更新数据（跳过周末）
* **新增 (Added)**: `--update` 参数 — 一次性运行数据更新
* **新增 (Added)**: `GET /update/stream` SSE 端点 — 实时推送数据更新进度到前端
* **新增 (Added)**: `GET /update/status` 端点 — 返回当前更新状态（running/stage/log）
* **修改 (Changed)**: `POST /update` 改为进程内 threading 运行（不再用 subprocess），支持状态追踪
* **修改 (Changed)**: `db/update.py` 全部 `print()` 替换为 `_log()` 共享状态机制，支持 web 实时读取
* **修改 (Changed)**: Watchlist 首页新增更新进度面板，实时显示 OHLCV/Fundamentals/Analyst/News/Sentiment 各阶段进度

### [2026-06-17] AI 分析引擎 + Skills 重写 + 情绪分析
* **新增 (Added)**: `ai_client.py` — OpenAI 兼容 AI 客户端，支持情绪分析和 Skills 分析两个独立 API 配置
* **新增 (Added)**: `skills/` 目录 — 重写 3 个 skills（longterm_screen, swing_screen, position_review），去除 market_regime_history 依赖，直接用本地 DuckDB 筛选数据
* **新增 (Added)**: `POST /analyze` 端点 — 本地调用 AI API 运行 Skills 分析，支持 longterm_screen / swing_screen / position_review
* **新增 (Added)**: `GET /analyze/status` 端点 — 返回 AI 配置状态和可用 Skills 列表
* **新增 (Added)**: `update_news_sentiment()` — 数据更新管道新增 AI 批量新闻情绪评分步骤
* **新增 (Added)**: `news` 表新增 `sentiment_score` 列（DOUBLE），配合已有的 `sentiment_label`
* **修改 (Changed)**: `.env.example` 扩展为完整配置模板，包含 `SENTIMENT_API_*`、`AI_API_*`、`FINNHUB_TIER` 等
* **修改 (Changed)**: `config.py` 新增 AI API 和 Finnhub 会员配置项
* **修改 (Changed)**: Finnhub rate limit 根据 `FINNHUB_TIER` 自动调整（free=1.1s, premium=0.12s）
* **修改 (Changed)**: Screener 页面左侧面板 w-72→w-80，右侧详情面板 w-80→w-420px
* **修改 (Changed)**: 右侧详情面板新增 Analyst Rating / Target / MSPR / Earnings Dates 展示
* **修改 (Changed)**: 右侧详情面板新增 AI Position Review 分析按钮
* **修改 (Changed)**: 导航栏新增 Long-Term / Swing AI 分析快捷按钮（仅在 AI 配置后显示）
* **修改 (Changed)**: `.skills/*.skill` ZIP 文件同步更新为去除 market_regime 依赖的版本

### [2026-06-17] Fundamentals 数据补全：分析师、财报日期、内部人交易
* **新增 (Added)**: `analyst_rating`、`analyst_target_price`、`analyst_count` 字段，数据来自 yfinance
* **新增 (Added)**: `next_earnings_date`、`last_earnings_date` 字段，数据来自 Finnhub earnings calendar API
* **新增 (Added)**: `mspr`（内部人净买入比率），基于 Finnhub Form 4 insider transactions 原始数据自主计算 90 天滚动 MSPR
* **修改 (Changed)**: `FinnhubFundamentalsProvider` 重写为混合数据源（Finnhub metrics/profile + yfinance analyst + Finnhub earnings/insider），每 ticker 5 次 API 调用
* **修改 (Changed)**: `update_fundamentals` SQL 扩展为 18 字段完整写入，与老项目 TTAiTradingSystem 数据结构对齐

### [2026-06-17] 新增前端股票筛选页面
* **新增 (Added)**: `/screener` 页面，支持按板块、趋势（MA200/MA50/均线多头排列）、RSI、距MA20距离、成交量比率、ATR波动率、近期涨跌幅等多维条件筛选全部 2150+ 支股票
* **新增 (Added)**: 筛选结果表格支持列排序、CSV 导出、点击行查看个股详情（Fundamentals + 新闻）侧边栏
* **新增 (Added)**: `_get_all_sectors()` 辅助函数，从 `stocks_meta` 获取全量板块列表
* **修改 (Changed)**: 首页和筛选页统一导航栏，支持 Watchlist / Screener 页面切换

### [2026-06-17] 数据管道架构重设计
* **新增 (Added)**: Provider 抽象层 (`data_sources/base.py`, `data_sources/us_market.py`, `data_sources/registry.py`)，支持按市场字段插拔数据源
* **新增 (Added)**: `stocks_meta` 表新增 `market` 和 `tier` 字段，支持 core/universe 分层
* **新增 (Added)**: CSV 股票池导入脚本 (`db/universe.py`)，支持导入数千支 ticker
* **新增 (Added)**: Web 筛选端点 `POST /screen`（按板块+技术条件本地 DB 筛选）和 `GET /ticker/{ticker}`（cache-first 详情）
* **新增 (Added)**: Windows 每日更新批处理脚本 (`db/run_daily_update.bat`)
* **修改 (Changed)**: `db/update.py` 重写为批量 OHLCV 下载 + 7 天 fundamentals 缓存 + tier 门槛新闻拉取
* **修改 (Changed)**: `db/fetch.py` 重写为基于 Provider 抽象层的初始数据拉取
* **修改 (Changed)**: `config.py` 新增 `FUNDAMENTALS_STALE_DAYS`、`NEWS_MOVER_PCT_THRESHOLD`、`NEWS_MOVER_VOLRATIO_THRESHOLD`、`OHLCV_BATCH_SIZE` 配置项

### [2026-06-11] 更新 OpenClaw 配置文件
* **修改 (Changed)**: 修改了 [openclaw.json](file:///D:/Dev_project/Python_Project/tt-trading-mcp/openclaw.json)，移除了已失效或不再使用的 `minimax` 与 `tencent` 提供商及其关联模型，新增了阿里云 `aliyun` 提供商，配置了 DashScope 兼容接口及 API Key，并增加了 `deepseek-v4-flash`、`deepseek-v4-pro`、`qwen3.7-plus`、`qwen3.7-max` 模型支持，同时将默认主模型（primary）设为 `aliyun/qwen3.7-plus`。

### [2026-06-11] 配置本地环境变量
* **新增 (Added)**: 创建了本地 [`.env`](file:///D:/Dev_project/Python_Project/tt-trading-mcp/.env) 配置文件，配置了 `FINNHUB_KEY`、`DB_PATH` 以及 `NEWS_DAYS`，完成项目运行所需的凭证配置。

### [2026-06-11] 新增并校正 Gemini 指南
* **新增 (Added)**: 创建了 [GEMINI.md](file:///D:/Dev_project/Python_Project/tt-trading-mcp/GEMINI.md)，提供关于本仓库系统结构、真正实现的 4 个 MCP 工具、DuckDB 数据表模式以及 Gemini 平台的优化和编码规范。
* **修改 (Changed)**: 基于对真实 codebase（[server.py](file:///D:/Dev_project/Python_Project/tt-trading-mcp/server.py)、[config.py](file:///D:/Dev_project/Python_Project/tt-trading-mcp/config.py)、[db/init.py](file:///D:/Dev_project/Python_Project/tt-trading-mcp/db/init.py)）的深入排查，在 [GEMINI.md](file:///D:/Dev_project/Python_Project/tt-trading-mcp/GEMINI.md) 中剔除了从 `CLAUDE.md` 继承而来的 SQLite 和多余占位工具等错误信息，校正为真实的 DuckDB 数据架构。
