# -*- coding: utf-8 -*-
"""由 TTAiTradingSystem 合并迁入的 27 个 MCP 工具测试。

只读工具走真实 Postgres（只读账号），写工具（notes / trader_daily_log）与
日志文件诊断工具使用 monkeypatch 验证 SQL 参数、返回值与降级路径，不污染线上表。
"""
import json

import config
from tools import journal_notes as jn
from tools import market_context as mc
from tools import trade_journal as tj
from tools._common import calc_mfe_capture_rate, normalize_direction, resolve_market


def test_common_helpers():
    assert normalize_direction("做多") == "LONG"
    assert normalize_direction("s") == "SHORT"
    assert normalize_direction(None) is None
    assert round(calc_mfe_capture_rate(0.0346, 6.92, 50.0), 4) == 0.5
    assert calc_mfe_capture_rate(-0.05, 5.0, -20.0) is None
    assert resolve_market("600519.SH") == "CN"
    assert resolve_market("AAPL") == "US"


def test_trade_journal_reads_real_db():
    recent = json.loads(tj.get_recent_trades(limit=3))
    assert len(recent) == 3
    first_id = recent[0]["display_id"]

    light = json.loads(tj.get_recent_trades_light(limit=5, status="closed", direction="L"))
    assert "schema" in light and "rows" in light
    assert len(light["rows"]) <= 5
    assert all(r[2] == "L" for r in light["rows"])

    details = json.loads(tj.get_trade_details(str(first_id)))
    assert details["display_id"] == first_id
    assert "executions" in details
    assert "deviation_flags" in details

    templates = json.loads(tj.get_strategy_templates())
    assert isinstance(templates, list)

    signals = json.loads(tj.get_strategy_signals(limit=2))
    assert isinstance(signals, list)

    assert "必须至少提供一个" in tj.get_option_market_snapshots()


def test_journal_notes_reads_real_db():
    assert isinstance(json.loads(jn.get_recent_notes(limit=3)), list)
    assert isinstance(json.loads(jn.get_daily_trade_notes(limit=3)), list)
    assert isinstance(json.loads(jn.get_trader_daily_logs(limit=3)), list)
    assert isinstance(json.loads(jn.get_trade_emotions(limit=3)), list)
    assert isinstance(json.loads(jn.get_trading_rules()), list)
    assert isinstance(json.loads(jn.get_energy_schedules()), list)
    assert isinstance(json.loads(jn.get_activity_sessions(limit=3)), list)


def test_journal_notes_writes_use_write_gateway(monkeypatch):
    calls = []

    def fake_fetch_write(sql, params=None):
        calls.append((sql, params))
        return [{"id": "note-uuid-1"}]

    monkeypatch.setattr(jn.pg, "fetch_write", fake_fetch_write)
    monkeypatch.setattr(jn.pg, "query_one", lambda sql, params=None: None)

    msg = jn.create_note("测试纪律笔记", "rule", trade_date="2026-11-17", source="web")
    assert "成功创建笔记" in msg
    assert "insert into notes" in calls[-1][0].lower()

    upd = jn.update_note("note-uuid-1", content="更新内容", trade_date="null")
    assert "成功更新笔记" in upd
    assert "trade_date = null" in calls[-1][0].lower()

    log_msg = jn.create_or_update_trader_daily_log("2026-11-17", mood_score=8, sleep_hours=7.5)
    assert "成功新建" in log_msg
    assert "insert into trader_daily_log" in calls[-1][0].lower()

    assert "不能为空" in jn.create_note("  ", "rule")
    assert "note_type" in jn.create_note("ok", "invalid_type")


def test_market_context_reads_real_db():
    assert isinstance(json.loads(mc.get_latest_market_breadth(limit=2)), list)
    regime = json.loads(mc.get_market_regime_history(limit=2, include_config=False))
    assert isinstance(regime, list)
    assert all("regime_config" not in r for r in regime)

    assert isinstance(json.loads(mc.get_daily_news_summary(limit=2)), list)
    assert isinstance(json.loads(mc.search_stock_news(ticker="NVDA", limit=2)), list)

    sent = json.loads(mc.get_news_sentiment_summary("NVDA", days=7))
    assert sent["ticker"] == "NVDA" and "total_news" in sent

    meta = json.loads(mc.search_stocks_meta(ticker="AAPL"))
    assert meta and meta[0]["ticker"] == "AAPL" and meta[0]["market"] == "US"

    ohlcv = json.loads(mc.get_stock_ohlcv("AAPL", limit=3))
    assert len(ohlcv) == 3 and ohlcv[0]["market"] == "US"

    fund = json.loads(mc.get_stock_fundamentals("AAPL"))
    assert fund["ticker"] == "AAPL" and fund["market"] == "US"


def test_system_log_tools(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "ttaisystem.log"
    log_file.write_text(
        "2026-11-17 09:30:00 - INFO - 启动后端 APScheduler\n"
        "2026-11-17 09:31:00 - INFO - Fetch VIX 成功保存\n"
        "2026-11-17 09:32:00 - ERROR - rate limit exceeded\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "TTAI_LOG_DIR", str(log_dir))

    tail = mc.get_system_logs(lines=2)
    assert "Fetch VIX" in tail and "rate limit" in tail

    report = mc.analyze_server_status(lines_to_scan=50)
    assert "WARNING" in report and "成功=1" in report and "频控=1" in report
