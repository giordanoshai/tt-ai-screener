# -*- coding: utf-8 -*-
"""core/quotes.py 的测试。

取数层，一律打桩不发真实请求。`tests/conftest.py` 已经默认拦住了
`core.akshare._get` 和 `core.uw._fetch`，这里再把更上层的函数换掉，
用固定数据把解析逻辑测透。

重点测三个实测出来的坑：
1. A股必须走新浪源 stock_zh_a_spot——AKTools 上东财源全部 500。
2. UW 返回的是**倒序**，第一行最新，取错方向会拿到一个月前的价。
3. 任何一路失败都只能影响那一路，不能让整轮挂掉。
"""
from unittest.mock import patch

from core import quotes


#: 新浪全市场快照的真实返回形状（601138 那一行）
SINA_ROW = {
    "代码": "sh601138", "名称": "工业富联", "最新价": 63.55,
    "涨跌额": 0.35, "涨跌幅": 0.554, "买入": 63.54, "卖出": 63.55,
    "昨收": 63.20, "今开": 63.84, "最高": 65.30, "最低": 63.30,
    "成交量": 61502500.0, "成交额": 3957047234.0, "时间戳": "11:30:00",
}

#: UW 1 分钟线的真实返回形状，**倒序**：第一行最新
UW_BARS = [
    {"close": "327.70", "high": "327.9", "low": "327.5", "open": "327.6",
     "start_time": "2026-09-03T23:59:00Z", "volume": 716,
     "total_volume": 37225806, "market_time": "po"},
    {"close": "328.21", "high": "328.5", "low": "327.9", "open": "328.0",
     "start_time": "2026-09-03T19:59:00Z", "volume": 571506,
     "total_volume": 37000000, "market_time": "r"},
    {"close": "324.85", "high": "325.0", "low": "324.0", "open": "324.2",
     "start_time": "2026-09-03T13:29:00Z", "volume": 8400,
     "total_volume": 450000, "market_time": "pr"},
]


class TestCnQuotes:
    def test_parses_sina_snapshot(self):
        with patch.object(quotes.akshare, "available", return_value=True), \
             patch.object(quotes.akshare, "_get", return_value=[SINA_ROW]):
            got = quotes.cn_quotes(["601138.SH"])
        q = got["601138.SH"]
        assert q["last"] == 63.55
        assert q["high"] == 65.30
        assert q["low"] == 63.30
        assert q["open"] == 63.84
        assert q["prev_close"] == 63.20
        assert q["session"] == "cn"
        assert q["source"] == "akshare"

    def test_uses_sina_endpoint_not_eastmoney(self):
        """AKTools 上所有 _em（东方财富）接口都返回 500，只有新浪源活着。"""
        with patch.object(quotes.akshare, "available", return_value=True), \
             patch.object(quotes.akshare, "_get",
                          return_value=[SINA_ROW]) as spy:
            quotes.cn_quotes(["601138.SH"])
        called = spy.call_args[0][0]
        assert called == "stock_zh_a_spot"
        assert not called.endswith("_em"), "东财源在这台 AKTools 上全是 500"

    def test_ticker_not_in_snapshot_is_absent(self):
        with patch.object(quotes.akshare, "available", return_value=True), \
             patch.object(quotes.akshare, "_get", return_value=[SINA_ROW]):
            assert quotes.cn_quotes(["000001.SZ"]) == {}

    def test_failure_returns_empty_not_raise(self):
        """取不到就返回空。conftest 默认把 _get 换成抛异常的桩，
        这条测的正是「被拦住之后不会把整轮搞挂」。"""
        with patch.object(quotes.akshare, "available", return_value=True), \
             patch.object(quotes.akshare, "_get",
                          side_effect=RuntimeError("boom")):
            assert quotes.cn_quotes(["601138.SH"]) == {}


class TestUsQuotes:
    def test_takes_first_row_because_uw_is_reverse_ordered(self):
        """UW 返回倒序，第一行才是最新的。取末行会拿到一个月前的价。"""
        with patch.object(quotes.uw, "available", return_value=True), \
             patch.object(quotes.uw, "get_many",
                          return_value={"/api/stock/AAPL/ohlc/1m": UW_BARS}):
            got = quotes.us_quotes(["AAPL"])
        assert got["AAPL"]["last"] == 327.70
        assert got["AAPL"]["session"] == "po"

    def test_high_low_span_the_whole_day(self):
        """当天最高最低要扫过所有分钟线，不能只看最新那一根——
        「假突破」的判定要用当天盘中最高价。"""
        with patch.object(quotes.uw, "available", return_value=True), \
             patch.object(quotes.uw, "get_many",
                          return_value={"/api/stock/AAPL/ohlc/1m": UW_BARS}):
            got = quotes.us_quotes(["AAPL"])
        assert got["AAPL"]["high"] == 328.5
        assert got["AAPL"]["low"] == 324.0

    def test_none_payload_is_skipped(self):
        with patch.object(quotes.uw, "available", return_value=True), \
             patch.object(quotes.uw, "get_many",
                          return_value={"/api/stock/AAPL/ohlc/1m": None}):
            assert quotes.us_quotes(["AAPL"]) == {}

    def test_index_tickers_are_skipped(self):
        """指数代码喂给 /api/stock/... 一律 422，发出去只是白等。"""
        with patch.object(quotes.uw, "available", return_value=True), \
             patch.object(quotes.uw, "get_many") as spy:
            assert quotes.us_quotes(["^VIX"]) == {}
        spy.assert_not_called()


class TestGetBothMarkets:
    def test_one_market_failing_does_not_kill_the_other(self):
        """A股那一路挂了，美股照常返回。"""
        with patch.object(quotes.akshare, "available", return_value=True), \
             patch.object(quotes.akshare, "_get",
                          side_effect=RuntimeError("boom")), \
             patch.object(quotes.uw, "available", return_value=True), \
             patch.object(quotes.uw, "get_many",
                          return_value={"/api/stock/AAPL/ohlc/1m": UW_BARS}):
            got = quotes.get({"CN": ["601138.SH"], "US": ["AAPL"]})
        assert "AAPL" in got
        assert "601138.SH" not in got
