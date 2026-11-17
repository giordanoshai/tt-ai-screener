# -*- coding: utf-8 -*-
"""review_tonight_trades 工具测试：覆盖真实 Postgres 查询与边界单测。"""
from datetime import datetime, timezone
import json

from tools import review_tonight_trades as rtt


def _run(**kwargs):
    return json.loads(rtt.review_tonight_trades(**kwargs))


def test_et_day_bounds_handles_est_and_edt():
    # 2026-11-17 为冬令时 (EST, UTC-5): 00:00 ET = 05:00 UTC
    target, start_utc, end_utc = rtt._et_day_bounds("2026-11-17")
    assert target.isoformat() == "2026-11-17"
    assert start_utc == datetime(2026, 11, 17, 5, 0, tzinfo=timezone.utc)
    assert end_utc == datetime(2026, 11, 18, 5, 0, tzinfo=timezone.utc)

    # 2026-07-15 为夏令时 (EDT, UTC-4): 00:00 ET = 04:00 UTC
    _, start_edt, end_edt = rtt._et_day_bounds("2026-07-15")
    assert start_edt == datetime(2026, 7, 15, 4, 0, tzinfo=timezone.utc)
    assert end_edt == datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)


def test_invalid_date_returns_error():
    out = _run(date="2026/11/17")
    assert "error" in out


def test_real_spread_trades_and_summary_math():
    out = _run(date="2026-11-17")
    assert out["data_gaps"] == []
    summary = out["summary"]
    trades = out["trades"]

    assert summary["date_et"] == "2026-11-17"
    assert summary["tz"] == "America/New_York"
    assert summary["account"] == "FT"
    assert len(trades) == summary["count"]
    assert len(trades) >= 10

    # 默认不包含 cancelled
    assert all(t["status"] != "cancelled" for t in trades)
    assert summary["cancelled"] >= 15

    # 当日单时间已精简为 HH:MM:SS，且不再逐笔重复 account/currency/false 字段
    first = trades[0]
    assert len(first["in_et"]) == 8
    assert "prior_day" not in first
    assert "partial_close" not in first
    assert "legs_in" in first and "legs_out" in first

    # 汇总金额必须与逐笔明细实算一致
    closed = [t for t in trades if t["status"] == "closed"]
    expected_pnl = round(sum(t["pnl"] for t in closed if t["pnl"] is not None), 2)
    assert summary["pnl_usd"] == expected_pnl
    assert summary["closed"] == len(closed)

    # 列表按入场时间升序
    entry_times = [t["in_et"] for t in trades]
    assert entry_times == sorted(entry_times)

    # include_cancelled=True 会把撤单也带出来
    with_cancelled = _run(date="2026-11-17", include_cancelled=True)
    assert len(with_cancelled["trades"]) == len(trades) + summary["cancelled"]

    # 当晚交易日志（notes.daily_trade_note=true）同时返回并清洗掉 HTML 标签
    notes = out["trade_notes"]
    assert summary["notes_count"] == len(notes)
    assert len(notes) >= 1
    assert any(n["id"] == "4fd42711-febc-43d5-acdd-1e6d7b97363b" for n in notes)
    assert all("<p>" not in n["content"] for n in notes)


def test_real_daily_log_included_when_present():
    out = _run(date="2026-11-13")
    assert out["data_gaps"] == []
    assert "daily_log" in out
    assert out["daily_log"]["log_date"] == "2026-11-13"
    assert "伽马墙" in out["daily_log"]["daily_review"]


def test_clean_html_helper():
    assert rtt._clean_html("<p>第一行</p><p>第二行<br/>第三行</p>") == "第一行\n第二行\n第三行"
    assert rtt._clean_html(None) == ""


def test_real_single_leg_ftt_trades_included():
    out = _run(date="2026-11-06")
    assert out["data_gaps"] == []
    ids = {t["id"] for t in out["trades"]}
    assert "FT-1926" in ids
    single = next(t for t in out["trades"] if t["id"] == "FT-1926")
    assert single["kind"] == "single"
    assert single["option_code"] == "SPY261106P00738000"
    assert "self_check" in single


def test_real_overnight_trade_matched_by_exit_time():
    # FT-1965 于 2026-11-09 15:39 ET 开仓，2026-11-10 09:31 ET 平仓
    out = _run(date="2026-11-10")
    ids = {t["id"] for t in out["trades"]}
    assert "FT-1965" in ids
    trade = next(t for t in out["trades"] if t["id"] == "FT-1965")
    assert trade["prior_day"] is True
    assert trade["in_et"].startswith("2026-11-09 ")


def test_default_date_uses_current_et_date():
    out = _run()
    expected_et = datetime.now(rtt.ET_TZ).date().isoformat()
    assert out["summary"]["date_et"] == expected_et
    assert "data_gaps" in out


def test_partial_close_mismatch_flag():
    legs = [
        {"side": "BO", "option_code": "SPY261116P00747000", "fill_price": "2.49"},
        {"side": "SO", "option_code": "SPY261116P00745000", "fill_price": "1.67"},
    ]
    close_legs = [
        {"side": "SC", "option_code": "SPY261116P00747000", "fill_price": "2.36"},
        {"side": "BC", "option_code": "SPY261116P00745000", "fill_price": "1.63"},
    ]
    assert rtt._is_partial_close_mismatch(legs, close_legs, 2, -10.0) is True
    assert rtt._is_partial_close_mismatch(legs, close_legs, 2, -18.0) is False


def test_one_table_failure_records_data_gap_without_crashing(monkeypatch):
    def fake_query(sql, params=None):
        if "ftt_spread_trades" in sql:
            raise RuntimeError("spread table unavailable")
        if "from notes" in sql:
            return []
        return [{
            "order_id": "FT-9999",
            "ticker": "QQQ",
            "direction": "long",
            "option_code": "QQQ261117C00660000",
            "qty": 1,
            "entry_mid": 1.0,
            "exit_mid": 1.2,
            "entry_time": datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc),
            "exit_time": datetime(2026, 11, 17, 15, 10, tzinfo=timezone.utc),
            "status": "closed",
            "realized_pnl": 20.0,
            "total_pnl": 20.0,
            "close_reason": "目标价止盈",
            "template_snapshot": {"name": "松一点"},
        }]

    monkeypatch.setattr(rtt.pg, "query", fake_query)
    monkeypatch.setattr(rtt.pg, "query_one", lambda sql, params=None: None)
    out = _run(date="2026-11-17")
    assert len(out["data_gaps"]) == 1
    assert "ftt_spread_trades" in out["data_gaps"][0]
    assert [t["id"] for t in out["trades"]] == ["FT-9999"]
    assert out["trade_notes"] == []
    assert out["summary"]["pnl_usd"] == 20.0
