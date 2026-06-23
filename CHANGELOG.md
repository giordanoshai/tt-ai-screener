# Changelog

All notable changes to this project will be documented in this file.

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
