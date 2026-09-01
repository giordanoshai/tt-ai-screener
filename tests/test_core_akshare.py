# -*- coding: utf-8 -*-
"""core/akshare.py —— A股小时线的实时取数。

这个模块存在的理由见它自己的模块注释，一句话：库里的 A股小时线只覆盖活跃池
（约 150 只），而活跃池是按「站上 50 日均线 + 放量」挑的，跟「用户想看哪只票」
是两回事。2026-09-01 实测茅台、五粮液、宁德时代、比亚迪库里一根都没有。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from core import akshare as ak

CN_TZ = timezone(timedelta(hours=8))


class TestToSina:
    """库里的写法带交易所后缀，新浪要的是前缀。"""

    @pytest.mark.parametrize("ticker,expected", [
        ("600519.SH", "sh600519"),
        ("000001.SZ", "sz000001"),
        ("430139.BJ", "bj430139"),
        ("600519.sh", "sh600519"),
    ])
    def test_converts(self, ticker, expected):
        assert ak.to_sina(ticker) == expected

    @pytest.mark.parametrize("bad", ["AAPL", "600519", "", "600519.XX"])
    def test_rejects_non_cn(self, bad):
        assert ak.to_sina(bad) is None


class TestParseMinute60:
    """★ `ts` 必须是这根 K 线【开始】的时刻 ★

    新浪标的是【结束】时刻——实测 sh600519 一次返回 1970 行，`day` 只出现
    10:30 / 11:30 / 14:00 / 15:00 四种，一行 09:30 都没有。A股 9:30 开盘，
    第一根覆盖 9:30~10:30 却被标成 10:30。

    本仓库和 tt-data-hub 统一存开始时刻，两个市场写进同一张表、共用同一个
    小时线视图，语义错一根，所有点位就整体错一格。
    """

    def test_end_time_is_converted_to_start_time(self):
        rows = ak.parse_minute_60([
            {"day": "2026-09-01 10:30:00", "open": "1", "high": "2",
             "low": "0.5", "close": "1.5", "volume": "100"},
        ])
        assert len(rows) == 1
        assert rows[0]["ts"] == datetime(2026, 9, 1, 9, 30, tzinfo=CN_TZ)

    def test_a_full_day_becomes_the_four_expected_starts(self):
        rows = ak.parse_minute_60([
            {"day": f"2026-09-01 {t}", "open": "1", "high": "2",
             "low": "0.5", "close": "1.5", "volume": "1"}
            for t in ("10:30:00", "11:30:00", "14:00:00", "15:00:00")
        ])
        got = [r["ts"].strftime("%H:%M") for r in rows]
        assert got == ["09:30", "10:30", "13:00", "14:00"]

    def test_sorted_oldest_first(self):
        rows = ak.parse_minute_60([
            {"day": "2026-09-01 15:00:00", "open": "1", "high": "2",
             "low": "0.5", "close": "9", "volume": "1"},
            {"day": "2026-09-01 10:30:00", "open": "1", "high": "2",
             "low": "0.5", "close": "1", "volume": "1"},
        ])
        assert [r["close"] for r in rows] == [1.0, 9.0]

    def test_duplicate_timestamps_are_collapsed(self):
        """重复时间戳留最后一条。库里主键含 ts，重复行会让整批写入失败。"""
        rows = ak.parse_minute_60([
            {"day": "2026-09-01 10:30:00", "open": "1", "high": "2",
             "low": "0.5", "close": "1", "volume": "1"},
            {"day": "2026-09-01 10:30:00", "open": "1", "high": "2",
             "low": "0.5", "close": "7", "volume": "1"},
        ])
        assert len(rows) == 1 and rows[0]["close"] == 7.0

    def test_row_with_a_missing_price_is_dropped(self):
        """四个价格缺一个就整根丢掉，不能拿 None 去算均线。"""
        rows = ak.parse_minute_60([
            {"day": "2026-09-01 10:30:00", "open": "1", "high": None,
             "low": "0.5", "close": "1", "volume": "1"},
        ])
        assert rows == []

    def test_nan_price_is_dropped(self):
        """NaN 既不是假值也不满足 <= 0，`if not x` 那种写法会放它过去。"""
        rows = ak.parse_minute_60([
            {"day": "2026-09-01 10:30:00", "open": "1", "high": "nan",
             "low": "0.5", "close": "1", "volume": "1"},
        ])
        assert rows == []

    def test_garbage_timestamp_is_dropped(self):
        assert ak.parse_minute_60([
            {"day": "不是时间", "open": "1", "high": "2",
             "low": "0.5", "close": "1", "volume": "1"},
        ]) == []

    def test_empty_input(self):
        assert ak.parse_minute_60([]) == []


class TestRowShapeMatchesTheDatabase:
    """实时拿到的行必须跟库里查出来的形状一样，否则调用方得写两套。"""

    REQUIRED = {"ts", "ts_local", "session", "open", "high",
                "low", "close", "volume", "market"}

    def test_has_every_column_the_sql_returns(self):
        rows = ak.parse_minute_60([
            {"day": "2026-09-01 10:30:00", "open": "1", "high": "2",
             "low": "0.5", "close": "1.5", "volume": "100"},
        ])
        assert self.REQUIRED <= set(rows[0])

    def test_session_is_rth(self):
        """A股没有盘前盘后，采集侧写的也是 RTH，两边必须一致。"""
        rows = ak.parse_minute_60([
            {"day": "2026-09-01 10:30:00", "open": "1", "high": "2",
             "low": "0.5", "close": "1.5", "volume": "100"},
        ])
        assert rows[0]["session"] == "RTH"

    def test_ts_local_is_naive_beijing_time(self):
        """库里那列是 `ts at time zone 'Asia/Shanghai'`，出来是不带时区的。"""
        rows = ak.parse_minute_60([
            {"day": "2026-09-01 10:30:00", "open": "1", "high": "2",
             "low": "0.5", "close": "1.5", "volume": "100"},
        ])
        assert rows[0]["ts_local"] == datetime(2026, 9, 1, 9, 30)
        assert rows[0]["ts_local"].tzinfo is None


class TestNeverRaises:
    """跟 core/uw.py 一个规矩：取不到就返回空，调用方退回数据库。"""

    def test_not_configured_returns_empty(self, monkeypatch):
        monkeypatch.setattr(ak.config, "AKSHARE_BASE_URL", "")
        assert ak.available() is False
        assert ak.hourly("600519.SH") == []

    def test_network_error_returns_empty(self, monkeypatch):
        monkeypatch.setattr(ak.config, "AKSHARE_BASE_URL", "http://x:8080")
        with patch.object(ak, "_get", side_effect=OSError("连不上")):
            assert ak.hourly("600519.SH") == []

    def test_non_cn_ticker_returns_empty(self, monkeypatch):
        """美股小时线来自 UW、已经采进库了，这条路只服务 A股。"""
        monkeypatch.setattr(ak.config, "AKSHARE_BASE_URL", "http://x:8080")
        assert ak.hourly("AAPL") == []

    def test_returns_the_newest_n_bars(self, monkeypatch):
        monkeypatch.setattr(ak.config, "AKSHARE_BASE_URL", "http://x:8080")
        raw = [{"day": f"2026-09-01 {h:02d}:30:00", "open": "1", "high": "2",
                "low": "0.5", "close": str(h), "volume": "1"}
               for h in range(1, 11)]
        with patch.object(ak, "_get", return_value=raw):
            rows = ak.hourly("600519.SH", bars=3)
        assert len(rows) == 3
        assert [r["close"] for r in rows] == [8.0, 9.0, 10.0]
