# PLAN.md — 合并 TTAiTradingSystem MCP 工具至 tt-trading-mcp

## 目标
将 `TTAiTradingSystem` ([mcp_tools.py](file:///D:/Dev_project/Python_Project/TTAiTradingSystem/mcp_tools.py)) 的交易复盘、心理笔记、市场宏观与运维诊断工具统一迁入 `tt-trading-mcp`，使用单一 FastMCP 服务对外提供全部工具，并剔除与现有工具重复的旧版自选/选股入口。

## 分步计划
- [x] **Step 1**: 抽取公共辅助函数（`tools/_common.py`）及配置项 `TTAI_LOG_DIR`（`config.py`），统一处理 `datetime`/`date` 紧凑 JSON 序列化、方向别名映射、MFE 捕获率计算及市场推断。
- [x] **Step 2**: 新增 `tools/trade_journal.py`（7 个工具：`get_recent_trades`、`get_recent_trades_light`、`get_trade_details`、`get_strategy_templates`、`get_strategy_signals`、`get_option_ohlcv`、`get_option_market_snapshots`）。
- [x] **Step 3**: 新增 `tools/journal_notes.py`（10 个工具：`get_recent_notes`、`get_daily_trade_notes`、`create_note`、`update_note`、`get_trader_daily_logs`、`create_or_update_trader_daily_log`、`get_trade_emotions`、`get_trading_rules`、`get_energy_schedules`、`get_activity_sessions`）。
- [x] **Step 4**: 新增 `tools/market_context.py`（10 个工具：`get_latest_market_breadth`、`get_market_regime_history`、`get_daily_news_summary`、`search_stock_news`、`get_news_sentiment_summary`、`search_stocks_meta`、`get_stock_ohlcv`、`get_stock_fundamentals`、`get_system_logs`、`analyze_server_status`），全部满足 `test_sql_rules.py` 规矩。
- [x] **Step 5**: 在 `server.py` 注册全部 40 个工具，补充单元测试与集成测试（`tests/test_merged_tools.py`），更新 `tests/test_main.py`、`AGENTS.md`、`README.md`、`CHANGELOG.md` 并执行测试验收。
- [x] **Step 6**: 升级 `tools/trade_journal.py` 与 `tools/review_tonight_trades.py`，支持自动识别/合并垂直与日历价差双腿与单腿，补充短合约标识 `contract` 与 `spreads`/`singles` 汇总统计，并全面精简 `get_recent_trades`、`get_recent_trades_light` 与 `get_trade_details` 返回体以降低 token 消耗。

## 剔除的重复/旧版工具（不迁入）
- `get_user_watchlist`、`add_to_watchlist`、`add_ticker_to_watchlist`、`remove_from_watchlist`：已由本仓库 `list_watchlist` / `add_to_watchlist` / `remove_from_watchlist` 覆盖。
- `screen_swing_candidates`、`screen_longterm_candidates`：已由本仓库 `scan_swing` / `screen_stocks` 取代。
