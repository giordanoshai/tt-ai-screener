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


class TestPickTradingDayIgnoresStrayDays:
    """「全市场最新交易日」不能由单独一只票定义。

    2026-09-01 真实踩到：`^VIX` 是指数不是股票，计算时段跟股票不同，
    美股还没开盘它就先有了一根当天的数据。当时 `max(date)` 拿到 2026-09-01，
    而那一天【全市场只有 ^VIX 一只】有数据；主查询再按这一天等值筛选，
    别的票全被排除——`screen_stocks(market="US")` 只返回 ^VIX 一只。

    A股那边同时是正常的（返回 20 只），因为 A股池子里没有这种指数。

    这不是偶发：每天从 VIX 出当日数据、到晚上 us_daily 跑完之间，
    也就是一天里的大部分时间，筛选器都是坏的。
    """

    def test_stray_single_ticker_day_is_skipped(self):
        rows = [{"date": "09-01", "n": 1},      # 只有 ^VIX
                {"date": "08-31", "n": 2091},
                {"date": "08-28", "n": 2099}]
        assert snapshot.pick_trading_day(rows) == "08-31"

    def test_normal_latest_day_wins(self):
        rows = [{"date": "08-31", "n": 2091},
                {"date": "08-28", "n": 2099}]
        assert snapshot.pick_trading_day(rows) == "08-31"

    def test_half_written_day_is_skipped(self):
        """采集跑到一半时当天只有几百只票，这时候该用前一个完整交易日。"""
        rows = [{"date": "09-01", "n": 400},
                {"date": "08-31", "n": 2091}]
        assert snapshot.pick_trading_day(rows) == "08-31"

    def test_slightly_smaller_day_still_counts(self):
        """正常波动（个别票停牌、退市）不该把整天判掉。"""
        rows = [{"date": "09-01", "n": 2050},
                {"date": "08-31", "n": 2099}]
        assert snapshot.pick_trading_day(rows) == "09-01"

    def test_empty_returns_none(self):
        assert snapshot.pick_trading_day([]) is None

    def test_single_day_returns_itself(self):
        """窗口里只有一天时，它就是最热闹的那天，必须返回它而不是 None。"""
        assert snapshot.pick_trading_day([{"date": "08-31", "n": 3}]) == "08-31"


def test_latest_trading_day_is_not_a_bare_max():
    """SQL 必须按天数票数，不能是裸的 max(date)——见上面那个类的说明。"""
    sql = snapshot._LATEST_DAY_SQL.lower()
    assert "max(date)" not in sql
    assert "count(*)" in sql and "group by" in sql
