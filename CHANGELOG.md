# Changelog

All notable changes to this project will be documented in this file.

### [2026-08-31] 五个交易场景工具上线，MCP 工具从 10 个变 12 个

* **新增 (Added)**: `core/` 取数层——`uw.py`（UW 实时客户端）、
  `snapshot.py`（日线技术面）、`scoring.py`（形态与评分）、
  `catalyst.py`（消息面）、`fundamental.py`（基本面）、
  `regime.py`（市场环境）、`cn_signals.py`（A股信号）。
* **新增 (Added)**: 五个场景工具——`review_positions` 持仓分析、
  `scan_swing` 波段扫描、`analyze_ticker` 个股分析、
  `detect_catalysts` 突发消息侦测、`watch_positions` 持仓盯盘。
  工具内部算完直接返回结论，不调用大模型。
* **新增 (Added)**: `tests/test_sql_rules.py`——五条查询规矩和
  「短线不许碰基本面」的回归测试，扫源码，不连库。
* **修复 (Fixed)**: `config.py` 读不到 UW 密钥。`.env` 里的键名是
  `UW_T_KEY` / `UW_T_ENDPOINT`，代码里读的是 `UW_API_TOKEN`，
  一直拿到空字符串。现在两套名字都认。
* **修复 (Fixed)**: `core/snapshot.py`、`core/fundamental.py` 交出来的比率
  字段单位是百分点（`3.18` 代表 3.18%），跟数据库原始存的小数（`0.0318`）
  差 100 倍。这个量级错位此前真实导致过三处结论算错：短线三种形态判断里
  有一种因为门槛按错的量级写，永远不会触发；中线基本面评分（满分 35 分）
  有 12 分因为同样原因全场归零；给用户看的营收增速把 `+106%` 显示成了
  `+1.1%`。现在两个模块统一对外交百分点，并在 `CLAUDE.md` 里补了一节说明
  这个单位约定，防止下一个人在这里再摔一次。
* **修改 (Changed)**: 下线 `analyze_watchlist`（被 `scan_swing(scope="pool")`
  取代）和 `analyze_positions`（被 `review_positions` 取代），函数一并删除。
* **修改 (Changed)**: `get_hourly_setup` 不再单独暴露为 MCP 工具，
  逻辑并进 `analyze_ticker`。函数保留在 `tools/hourly.py`，网页面板还在用。
* **修改 (Changed)**: `CLAUDE.md` 的查询规矩从四条变五条，
  补上 `stock_ohlcv_min` 的三种周期（`1h`/`1m`/`5m`）必须带 `timeframe` 条件。

#### 最终评审修复轮（同日）

* **修复 (Fixed)**: **同一只票，两个工具给出打架的结论。** 持仓分析判断
  「跌破止损」时只看有没有站回 20 日均线，不看是盈是亏、也不看破了多久，
  结果 AMZN（浮盈 8.3%、只跌破 0.17%）拿到最高紧急度 90、文案「按纪律该
  减仓或清掉」，而同一天持仓盯盘对同一只票说的是「移动止损被触及、还在
  浮盈 204 美元、不必马上动」。现在三分类（浮盈触线 / 最近击穿 / 长期在
  止损位下方）由 `core/scoring.classify_stop_breach()` 统一判定，两个工具
  共用同一个函数、同一个「连续破位多少天」的界值，结论不可能再打架。
* **修复 (Fixed)**: **一路数据源报错就把整个工具打死。** 持仓分析、波段
  扫描、个股分析三个工具原本整个函数只有一个 try，新闻、期权流、市场环境
  任意一路抛异常，返回体就只剩一个 error，一条结论都不给——只覆盖约 40 只
  大盘股、注释自己写着「缺了不致命」的 gamma 墙也能干掉整只票的体检。
  现在每一路各自 try/except，挂了只写进 `data_gaps`，其余照常给结论。
* **修复 (Fixed)**: **「创 20 个交易日新低」漏报 96%。** 窗口把当天自己也
  算进了最低价，再拿收盘价去比——数学上只有「收盘价正好等于当日最低价」
  才成立，实测 2082 只美股命中 9 只，按真实语义应该 218 只。这是盯盘工具
  里的安全告警，而这个工具的信条是「没事就说没事」，它的沉默会被当成安全。
  A股市场宽度的「创 20 个交易日新高」是同一个 bug（155 vs 316），一并修。
  原来的回归测试用同一个错误条件挑候选票，所以永远绿，一并重写。
* **修复 (Fixed)**: **缺一半依据的评分照样打高分，且低置信度提示恰好被
  挡住。** 平安银行走中线档时基本面 35 分 + 相对强度 20 分整块被移出分母，
  剩下 45 分打出 82 分，而基本面齐全的 ORCL 只有 77 分——两个数放在一起
  会让人把平安银行排在 Oracle 前面。评分结果现在带 `evidence_pct`（这个分
  用上了百分之多少的评分表），低于 60% 时另加一条明写的提醒；个股分析里
  那条置信度提示原来写成 `if plan and (...)`，没识别出形态时整条被跳过，
  而「没形态 + 缺一堆依据」恰恰是最该警告的一类，现在跟 plan 解耦。
* **修复 (Fixed)**: 波段扫描的评分依据（库里 5 天的期权归档）被后补的实时
  期权流覆盖到同一个键上，两者都不带标签，出现过「理由写净 −10%、旁边
  证据是净 −43.7%」。现在两份分开放、各自标明来源、时间窗和用没用来打分。
* **修复 (Fixed)**: 「大盘综述不算个股消息」的过滤只做了两个工具。NVDA 靠
  一条挂 7 只票的行业综述在波段扫描里拿到消息面满分，ORCL 靠一条挂 8 只票
  的综述把紧急度从 20 抬到 65。阈值收进 `core/catalyst.py` 只定义一次，
  五个工具共用。
* **修复 (Fixed)**: 盯盘会为 1 万美元的期权异动发告警（分位数门槛按定义
  每只票每天都有约 10% 的行满足，等于保证天天有货）。加了绝对下限 25 万
  美元（硬门槛的一半，按真实分布定），低于它整条不发。
* **修复 (Fixed)**: 「底部反转」不要求价格真在底部，把离 52 周高点只有 6%
  的 MSFT 标成底部反转（全市场命中 353 只，其中多只贴着 52 周新高）。
  补两条判据：离 52 周高点至少 15%、且收盘价高出 200 日均线不超过 15%。
* **修复 (Fixed)**: 个股分析原来是五个工具里唯一没有 `warnings` 的，
  采集任务挂了它一个字不提；持仓分析的新闻按票复制整条原始记录；
  三个工具的价格和 RSI 没有四舍五入；波段扫描的 `as_of` 取第一只票的日期
  （停牌票排在首位会给整批结果贴错日期）、且缺 `truncated`/`returned_count`；
  盯盘在新闻标题缺失时把「None」直接印给用户。
* **修改 (Changed)**: 波段扫描的财报排雷窗口从 3 改成 5——模块文档声明的是
  「财报 3 个交易日内直接剔除」，而 `days_until` 是自然日，3 个交易日跨周末
  是 5 个自然日。三个工具的财报窗口为什么不一样，理由写进了常量注释。
* **修改 (Changed)**: `CLAUDE.md` 里「`sector` 大约覆盖一半」改成实测数字
  ——34683 行里只有 2020 行有值（5.8%），A股一行都没有。

### [2026-08-30] A 股四张信号表已建好，设计文档第五、六节改写

* **修改 (Changed)**: A 股信号表已在 `tt-data-hub` 建好并挂上定时（那边提交
  `26ad54e`），不再是「待补」。原计划列的 10 张待建表，实际动手时发现同花顺
  的接口**一次给全市场**，原来要逐票调 1500 次的活变成一次请求，于是 P0 和
  P1 合并成 4 张表、2 个任务：`cn_fund_flow_daily`（个股资金流，5208 只）、
  `cn_board_flow_daily`（行业 90 + 概念 387 个板块）、`cn_limit_pool_daily`
  （涨停板）、`cn_lhb_daily`（龙虎榜）。「板块轮动」因此白捡——不用先建成分股
  表再汇总。
* **修改 (Changed)**: 第十节分期说明更新——第二期不再被数据卡住，可以接着
  第一期做。
* **修复 (Fixed)**: 记下两个影响下游查询的单位口径问题（采集端已统一处理）：
  板块资金流原始单位是**亿元**而个股资金流是「1.04亿」这种字符串，库里统一
  存**元**；同花顺把股票代码当**数字**返回，库里统一存 `002190.SZ` 这种补过
  前导零的标准格式。
* **修改 (Changed)**: 第六节「挂掉的接口」一节改写——**判据不是 `_em` 后缀**。
  东财的「全市场排名/实时快照」类全挂，「按日期查明细」类正常，新浪/百度/
  同花顺全部正常。按后缀一刀切会挡掉龙虎榜和涨停板，而这两个正是在用的。

### [2026-08-30] 设计文档补充数据来源策略，核对数据就绪情况

* **新增 (Added)**: 设计文档第二节加入「什么走数据库、什么走实时 UW」的分工表。
  实测 UW 代理接口耗时 8~12 秒、限额剩余百万次/分钟——**约束是延迟不是限额**。
  全市场扫描 2082 只美股走数据库 0.1 秒、走 UW 约 15 分钟，故批量扫描留在数据库；
  持仓盯盘改为**实时 UW 为主**（数据库里的期权异动每天只灌两次，做盯盘名不副实）。
* **新增 (Added)**: 记下一个实现必踩的坑——**UW 代理挡在 Cloudflare 后面，请求头
  不带浏览器 User-Agent 会被 403 拒掉**（Error 1010），与 token 无关。
* **修改 (Changed)**: 第六节「美股待修」按 2026-08-30 实况重写。`us_news` 采集已
  修复；小时线覆盖此前判断有误——155 只自选中 154 只、6 只持仓全部都有小时线，
  实际够用。新增记录 `cn_fundamentals` 的字段溢出 bug、`us_universe` 熔断阈值过紧。
* **修改 (Changed)**: 第十节标注数据就绪情况——第一期所需美股数据全部到位可以开工，
  第二期所需的 10 张 A 股表当前一张都未建。

### [2026-08-29] 五个交易分析 skill 的设计文档

* **新增 (Added)**: `docs/superpowers/specs/2026-08-29-五个交易分析skill设计.md`。
  把项目从「10 个通用工具」重做成 5 个围绕实际交易场景的 MCP 工具：持仓分析、
  波段扫描（盘后）、个股分析、突发消息侦测、持仓盯盘。工具内部把结论算完直接返回，
  不再由服务端调大模型生成报告。底下抽出 `core/` 五个可复用取数模块。
* **新增 (Added)**: 文档第三节给出**短线与长线的数据分界表**——短波段只看价格走势、
  资金流向和有没有雷，市盈率、营收增长、毛利率、分析师目标价一律禁用，且在代码里
  按档拆分评分函数，写错会直接报错而不是悄悄用上。
* **新增 (Added)**: 文档第六节是给 `tt-data-hub` 的 A 股补数清单。实测了局域网
  AKTools 服务（AKShare 1.18.64）的十余个接口，按 P0/P1/P2 列出 9 张待建表和字段，
  并明确列出**不采**的品类（债券、外汇、加密货币、期货、Tick 数据、宏观指标、
  港股等）及理由。
* **修复 (Fixed)**: 排查发现三处与文档不符的实际情况——`stock_ohlcv_min` 里还有
  第三种周期 `5m`（美股 98 万行，原文档只写了 `1h`/`1m`）；美股盘前盘后分钟数据
  近 5 天只剩 18 只票（历史曾覆盖 301 只）；`us_news` 采集任务当日为 `failed`。
  已作为待修项写入设计文档。

### [2026-08-29] README 与 .env.example 重写，补回自选池漏掉的两只票

* **修改 (Changed)**: **`README.md` 整篇重写**。旧版还在讲 yfinance + Finnhub 采集、
  本地 DuckDB 文件、`--update` / `--cron` 参数、9 个 MCP 工具——这些东西现在一样都
  不存在了，照着它做没有一步是对的。新版开篇就讲清楚数据在哪、谁写的：一张
  「采集端 tt-data-hub 写 → 自建 Postgres → 本项目只读消费」的图，两张表格分别列
  **只读的 9 个数据源**（每张表的规模、装什么、坑在哪）和**本项目只写的 3 张表**，
  再加一张「不在数据库里的配置」（模型清单、密钥、提示词、语言各自在哪个文件）。
  另外把写查询必须守的四条规矩、10 个 MCP 工具、6 个网页页面、目录结构都更新到
  与代码一致。
* **修改 (Changed)**: **`.env.example` 重写**。旧版**根本没有 `PG_READ_URL` 和
  `PG_WRITE_URL`**——照着它配的人连不上数据库，却留着 `FINNHUB_KEY` /
  `DB_PATH=./trading.duckdb` / `NEWS_DAYS` 这些早已没有任何代码引用的变量
  （`tests/test_pg.py` 里有条测试专门钉住「config 里不许再出现这些名字」）。
* **修复 (Fixed)**: **自选池补回 AAPL 和 SPCX**。旧 DuckDB 的自选池有 6 只票，
  线上那 153 行来自另一套系统，比对后发现这两只线上没有。两只都在 `stocks_meta`
  里、都有日线，属于迁库时漏掉的，补进去之后自选池 155 只。
* **修改 (Changed)**: 核对旧 `trading.duckdb` 与线上库的覆盖情况，确认可以删除
  （详见下方 2026-08-29 那条里的保留说明）：9 个模型、3 条分析历史、6 笔持仓
  都已迁走；新闻的时间窗（2026-06-03 ~ 07-16，5276 条）完整落在线上 8.0 万条
  （2024-12 起）里面；日线线上是 2021-02 起、更新到 2026-08-28，比旧库的
  2024-07 ~ 2026-07 又长又新。唯一不重合的是 199 只已经掉出「市值 20 亿美元
  以上」股票池的票，选股本来就不看它们。

### [2026-08-29] 四张漏迁的表各归其位，从旧 DuckDB 抢回 6 笔真实持仓

改造成只读 Postgres 消费端时，有四张表被漏掉了——它们在线上库里**根本不存在**，
所以对应功能一直是坏的，而界面上看起来只是「没配置」。

* **修复 (Fixed)**: **从 `trading.duckdb` 里抢回 6 笔真实持仓**（GOOGL / AMZN /
  MP / RKLB / NVO / ORCL），而线上 `user_positions` 当时是**空的**。差一点就
  跟着旧库一起删掉了。迁进 Supabase 之后接上现价验证过整条链路：成本
  11935.6 → 现值 10961.3（−8.16%）。新表比旧的多两列 `market` 和 `currency`，
  所以汇总是**分币种**的，美股美元和 A股人民币不会被加到一起。
* **新增 (Added)**: `sql/001_analysis_history.sql` —— 建 `analysis_history` 表。
  这是本项目自己的数据（不是行情），迁库时漏了，所以「分析历史」页面一直是空的。
  `write_user` 有建新表的权限（改已有表不行——那些表属主是 postgres），
  所以这个文件不需要管理员执行。旧库里 3 条历史记录已迁入。
* **修改 (Changed)**: AI 模型配置从 `ai_models` 表搬到 `config/models.json`。
  旧库里 9 个模型配置已导出。**密钥不在 JSON 里**——每个模型只带一个
  `api_key_env` 字段指向环境变量名，实际值在 `.env`。改模型直接改文件，
  网页上的模型页改成只读。
* **修改 (Changed)**: 自定义技能提示词从 `skill_prompts` 表搬到
  `config/skill_prompts.json`。文件不存在就直接用 `skills/*.md`，第一次在
  设置页保存时才生成。「重置」= 删掉 JSON 里那一条。
* **移除 (Removed)**: 语言切换整个删掉（原来存 `app_settings` 表）。
  现在只看环境变量 `LANG_UI`。
* **移除 (Removed)**: `/update`、`/update/status`、`/update/stream` 三个端点，
  连同 `home.html` 的 UPDATE 按钮、`index.html` 的更新按钮、以及浮动的管道
  日志面板。采集搬到 `tt-data-hub` 之后这些都是假按钮，点了只会报错。
* **修复 (Fixed)**: 分析历史现在在 `/analyze/stream` 里也存。原计划只在
  `/analyze` 存，但网页上三个页面点「分析」走的**全是**流式那条，只存
  `/analyze` 等于历史页永远是空的。存历史失败只记日志，不影响分析本身
  ——结果已经返回给用户了。
* **修改 (Changed)**: 摘掉最后一个 `xfail` 标记。`stocks_meta.sector` 补到
  2020 只（选股池覆盖 2018/2121 = 95%），越过了那条测试等的门槛。补的
  过程有波折：第一轮撞上 UW 转售端点整体故障（全域 530 约 20 分钟），
  2133 只里 530 只没采到只补到 1555；并发从 6 降到 3 重跑后失败数降到 7。
  `ticker_over20b` 那段临时兜底**暂时保留**（还有约 100 只票靠它），
  另加了一条探针测试，等覆盖率到 100% 会提醒可以删。
* **修改 (Changed)**: `.env.example` 补上 `DASHSCOPE_API_KEY` /
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `LANG_UI` 四个键并说明用途。
* **修改 (Changed)**: `CLAUDE.md` 新增完整的数据来源结构表——什么数据、
  存在哪、谁写。本仓库只写三张表（`user_watchlist` / `user_positions` /
  `analysis_history`），行情类一律只读。

**当前状态**：9 个模型里 4 个 Claude 和 1 个 GPT **拿不到密钥**（`.env` 里没有
`ANTHROPIC_API_KEY` 和 `OPENAI_API_KEY`），设置页会给它们标红「No key」——
这是诚实显示不是故障。默认的两个（分析走 Qwen3.7 Max、情绪走 DeepSeek V4
Flash）密钥齐全，能用。

**`trading.duckdb`（277 MB）暂时保留**，等确认新的模型配置和分析历史页面
都正常再删。`migrate_from_duckdb.py` 是配套的一次性脚本，同样等删。


### [2026-08-29] 删掉 DuckDB 与全部采集代码（计划 2 的任务 6）

采集职责移交 `tt-data-hub` 之后，这个项目里的采集代码整个删掉，四个
「import 就炸」的模块全部修好。316 个测试全绿。

* **移除 (Removed)**: `data_sources/` 整个目录、`db/init.py`、`db/update.py`、
  `db/fetch.py`、`db/universe.py`、`db/fetch_universe.py`、`db/migrate.py`、
  `db/run_daily_update.bat`。`db/` 下现在只剩 `pg.py`。
* **移除 (Removed)**: `main.py` 从 180 行砍到 49 行——去掉 `--update`、`--cron`、
  整个定时线程，以及 2026-07-16 加的百炼异步情绪打分后台线程（它依赖
  `db.update`）。现在只剩 `--mcp` 和 `--port`。
* **修改 (Changed)**: `skills/__init__.py` 改连 Postgres。按代码取数时先按后缀
  把票分成美股/A股两组分别查，不会串市场；取最新交易日改成票少的按票走主键
  倒扫、扫全池的先单查该市场最新日再等值比较，原来的相关子查询全删。
  另外改成**以传进来的代码列表为驱动表**——原来 `FROM stock_ohlcv_daily JOIN
  stocks_meta` 的写法，一只票只要缺行情或缺元数据就整行消失，而自选池是用户
  自己维护的，票被数据缺失悄悄吞掉比返回一行空数据更糟。
* **修改 (Changed)**: `web/app.py` 全部重写。**凡是 `tools/` 里已有的能力，端点
  改成直接调那个函数**，不再维护第二份 SQL：持仓的增删改查、自选的增删、
  板块下拉、最新交易日、行业别名展开。`/api/positions` 现在把工具算好的
  **分币种汇总**原样透出，网页层不再把美元和人民币加到一起。
  `/screen` 因为支持的筛选条件是 MCP 工具的超集（多头排列、离均线距离上下限、
  振幅、涨跌幅区间、是否在自选池里），仍然自己拼 SQL，但复用了工具里的
  两段式取最新交易日和行业别名展开。
* **修复 (Fixed)**: `ai_client.py` 的 `get_model()` / `list_models()` 一直在查
  `ai_models` 这张表，而**线上 Postgres 根本没有它**——所以 `get_model()` 永远
  返回 None，网页里的 AI 分析是完全不能用的，表现却是「没配置模型」，会让人
  以为是自己配错了。改成直接从环境变量读（`config.py` 里本来就有
  `AI_API_BASE` / `AI_API_KEY` / `AI_MODEL` 和一套 `SENTIMENT_*`）。
* **修复 (Fixed)**: 只读连接加 `statement_timeout = 3min`。2026-08-29 实测：
  被 kill 掉的进程发出去的查询在**数据库端继续跑了 2~3 小时**（后端进程不会
  跟着客户端死），其中一条正是本项目改造前那个 `group by market` 的全表扫写法，
  一直在扫 380 万行的日线表，把整个库的 IO 拖慢，而进程列表里看不出任何异常。
  排查还多绕一圈：`write_user` 没权限终结 `readonly_user` 的连接，只能用同一个
  角色去 `pg_cancel_backend`。**写连接不加**——大批量写入正常就要跑几分钟。
* **修改 (Changed)**: `/status` 的行数改用 Postgres 自己的统计值
  （`pg_class.reltuples`）而不是真数一遍全表，首页从 1124 毫秒降到 251 毫秒，
  误差千分之四。这个数字没有任何模板在显示，不值得为它花 425 毫秒/市场。
* **修改 (Changed)**: `/ticker/{ticker}` 去掉「库里没有就现场采一份写回库」的
  回落路径——那是采集，现在归 `tt-data-hub`。有测试钉住这个端点一个字都不写库。
* **新增 (Added)**: `pytest.ini` 限定 `testpaths = tests`。根目录下未跟踪的
  `scratch/` 里有一批同样叫 `test_*.py` 的临时脚本，**在 import 阶段就去调
  Unusual Whales 接口**，不限定目录的话光收集用例就要 15 分钟。
* **修改 (Changed)**: `requirements.txt` 删掉 `duckdb` / `yfinance` /
  `pandas-ta` / `finnhub-python`（最后那个全项目一次 `import finnhub` 都没有，
  本来就是死依赖），补上 `psycopg2-binary`。
* **修改 (Changed)**: 摘掉 9 个 `xfail` 标记——它们当初标的是「等 tt-data-hub
  灌数据」，现在 A股日线 105 万行、A股小时线 14.4 万行、美股小时线 2.9 万行、
  行业分类 1555 只都到位了，这些测试变成 XPASS。摘掉标记它们才重新是守卫。
  行业分类那条保留 xfail（只补到 1555，目标 2000，因为 `meta_refresh` 那轮撞上
  UW 端点整体故障，2133 只里 530 只没采到）。
* **修改 (Changed)**: `web/templates/settings.html` 删掉「数据更新时间表」卡片、
  `home.html` 删掉「AUTO 17:30」角标——采集不在这个项目里了，留着是假按钮。

**已知未完成 / 待决定**：任务 7（部署与 Cloudflare Tunnel）没做；网页上还有
一批端点的底层表线上不存在（`ai_models` / `app_settings` / `skill_prompts` /
`analysis_history`），对应的「改语言」「自定义技能提示词」「分析历史」页面
是摆设，删还是保留待定；`tools/watchlist.py` 的 `list_watchlist` 实测 1281 毫秒
（用的是已知较慢的 `group by` 写法），`/watchlist` 页面 1414 毫秒里 1281 毫秒
花在这；`analysis.html` 的总盈亏 getter 会把不同币种加到一起，等有了 A股持仓
就会显示假数字。


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
