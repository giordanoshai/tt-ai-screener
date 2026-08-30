# -*- coding: utf-8 -*-
"""技术面快照。走真实只读连接，跟现有测试一个路子。"""
import datetime

import pytest

from core import snapshot

REQUIRED_FIELDS = {
    "ticker", "market", "date", "close", "ma_20", "ma_50", "ma_200",
    "above_ma20", "above_ma50", "rsi_14", "vol_ratio", "atr_14", "atr_pct",
    "high_20", "high_55", "prior_high_20", "dist_from_20d_high_pct",
    "ma_50_slope_pct", "had_vol_up_3d", "made_20d_high_recently",
    "dollar_volume", "stale", "days_since_bar",
}


def test_latest_trading_day_is_a_date():
    day = snapshot.latest_trading_day("US")
    assert isinstance(day, datetime.date)
    assert day >= datetime.date(2026, 1, 1)


def test_latest_trading_day_per_market_differs_or_matches_but_never_none():
    assert snapshot.latest_trading_day("CN") is not None


def test_get_returns_one_entry_per_requested_ticker():
    out = snapshot.get(["NVDA", "AAPL"], "US")
    assert set(out) == {"NVDA", "AAPL"}
    assert REQUIRED_FIELDS <= set(out["NVDA"])


def test_get_unknown_ticker_is_none_not_missing_key():
    """要的票没有行情就是 None，但键必须在——调用方不用写 try。"""
    out = snapshot.get(["NVDA", "NOSUCHTICKERXYZ"], "US")
    assert out["NOSUCHTICKERXYZ"] is None
    assert out["NVDA"] is not None


def test_get_empty_list_returns_empty_dict():
    assert snapshot.get([], "US") == {}


def test_get_marks_stale_bars():
    out = snapshot.get(["NVDA"], "US")["NVDA"]
    assert isinstance(out["stale"], bool)
    assert out["days_since_bar"] >= 0


def test_scan_us_returns_liquid_universe():
    rows = snapshot.scan("US")
    assert len(rows) > 500
    assert all(REQUIRED_FIELDS <= set(r) for r in rows[:20])
    assert all(r["market"] == "US" for r in rows)


def test_scan_cn_returns_rows_and_never_mixes_markets():
    rows = snapshot.scan("CN")
    assert len(rows) > 100
    assert all(r["market"] == "CN" for r in rows)
    # A股代码一定带交易所后缀，混进美股就会露馅
    assert all(r["ticker"].endswith((".SH", ".SZ", ".BJ")) for r in rows)


def test_scan_all_rows_are_on_the_latest_trading_day():
    day = snapshot.latest_trading_day("US")
    rows = snapshot.scan("US")
    assert {r["date"] for r in rows} == {day}


def test_scan_bad_market_raises():
    with pytest.raises(ValueError):
        snapshot.scan("HK")
