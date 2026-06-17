# Changelog

All notable changes to this project will be documented in this file.

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
