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

#: UW 1 分钟线的真实返回形状，**倒序**：第一行最新，同一交易日
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

#: UW 多日数据（跨越多个美东交易日）。
#: 美东 2026-09-02 最后一根 + 美东 2026-09-03 所有根
#: 注意：UW 返回倒序，第一行是最新的
#: UTC 20:00 (美东 16:00，09-03 最晚) vs UTC 13:30 (美东 09:30，09-03 最早)
UW_BARS_MULTIDAY = [
    # 美东 2026-09-03 的数据（最新交易日最晚根）
    {"close": "330.81", "high": "330.81", "low": "323.35", "open": "327.20",
     "start_time": "2026-09-03T20:00:00Z", "volume": 1000,
     "total_volume": 1000000, "market_time": "r"},
    # 美东 2026-09-03 的数据（最新交易日最早根 - 应该被用于 open）
    {"close": "328.40", "high": "328.40", "low": "323.53", "open": "324.62",
     "start_time": "2026-09-03T13:30:00Z", "volume": 800,
     "total_volume": 800000, "market_time": "r"},
    # 美东 2026-09-02 的数据（前一个交易日，high/low/open 不应该用这个）
    {"close": "317.15", "high": "317.15", "low": "316.71", "open": "316.90",
     "start_time": "2026-09-02T23:50:00Z", "volume": 600,
     "total_volume": 600000, "market_time": "r"},
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

    def test_goes_through_the_public_snapshot_helper(self):
        """走 akshare 的公开函数，不越层调它的私有 `_get`。

        超时该给多少（快照 60 秒、小时线 20 秒）是那个模块自己的事，
        取数方不该知道，也不该替它决定。
        """
        with patch.object(quotes.akshare, "available", return_value=True), \
             patch.object(quotes.akshare, "spot_snapshot",
                          return_value=[SINA_ROW]) as spy:
            got = quotes.cn_quotes(["601138.SH"])
        spy.assert_called_once()
        assert "601138.SH" in got

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

    def test_high_low_span_latest_trading_day(self):
        """当天（最新交易日）最高最低要扫过所有分钟线，不能只看最新那一根——
        「假突破」的判定要用当天盘中最高价。这条用单交易日数据测试。"""
        with patch.object(quotes.uw, "available", return_value=True), \
             patch.object(quotes.uw, "get_many",
                          return_value={"/api/stock/AAPL/ohlc/1m": UW_BARS}):
            got = quotes.us_quotes(["AAPL"])
        assert got["AAPL"]["high"] == 328.5
        assert got["AAPL"]["low"] == 324.0

    def test_high_low_open_only_from_latest_trading_day(self):
        """高低价和开盘价只反映最新交易日的数据，不跨日。

        UW 返回的数据可能跨越多个美东交易日，但 high/low/open
        只应该反映最新（最后）那一个交易日的值。

        多日数据中：
        - 美东 2026-09-02：high 317.15, low 316.71, open 316.90
        - 美东 2026-09-03（最新）：high 330.81, low 323.35, open 324.62

        正确答案应该是最新日的值，不是扫全部得出的 high 330.81, low 316.71。
        """
        with patch.object(quotes.uw, "available", return_value=True), \
             patch.object(quotes.uw, "get_many",
                          return_value={"/api/stock/AAPL/ohlc/1m": UW_BARS_MULTIDAY}):
            got = quotes.us_quotes(["AAPL"])
        # high 应该是最新交易日的最高价，不是全部数据的 330.81（碰巧相同）
        assert got["AAPL"]["high"] == 330.81
        # low 应该是最新交易日的最低价 323.35，不是全部数据的 316.71
        assert got["AAPL"]["low"] == 323.35, \
            "跨日数据中只用最新交易日，low 应该是 323.35 而非前一天的 316.71"
        # open 应该是最新交易日最早那根的开盘价 324.62，不是全部数据末尾的 316.90
        assert got["AAPL"]["open"] == 324.62, \
            "open 应该是最新交易日最早那根的开盘价 324.62，不是 316.90"

    def test_unparsable_times_fall_back_to_the_newest_bar_only(self):
        """所有 start_time 都解析不出来时，只用最新那一根算 high/low。

        以前这里回退成「扫全部原始行」，后果很隐蔽：假如哪天上游把
        `start_time` 换成时间戳整数、或者改了字段名，「当天最高价」就会
        静悄悄变成「一个月最高价」。而「假突破」的判定条件正是
        「当天最高价越过关键位、现价又跌回下方」——一个月最高价几乎必然
        高于任何一道近档阻力，于是每只美股持仓每天都会误报假突破。

        下面两根线的 start_time 都是坏的，一根 high=101、一根 high=300，
        属于不同日子。正确答案是 101（最新那根），不是 300。
        """
        broken = [
            {"close": "100.0", "high": "101.0", "low": "99.0", "open": "99.5",
             "start_time": 1789000000, "volume": 10,      # 整数，不是 ISO 字符串
             "total_volume": 1000, "market_time": "r"},
            {"close": "290.0", "high": "300.0", "low": "280.0", "open": "285.0",
             "start_time": None, "volume": 20,
             "total_volume": 2000, "market_time": "r"},
        ]
        with patch.object(quotes.uw, "available", return_value=True), \
             patch.object(quotes.uw, "get_many",
                          return_value={"/api/stock/AAPL/ohlc/1m": broken}):
            got = quotes.us_quotes(["AAPL"])
        assert got["AAPL"]["high"] == 101.0, \
            "解析不出日期时只能用最新那一根，不能把一个月最高价当成当天最高价"
        assert got["AAPL"]["low"] == 99.0
        assert got["AAPL"]["open"] == 99.5

    def test_uses_the_longer_ohlc_timeout(self):
        """拉 K 线实测单次 12.9~14.0 秒，贴着默认的 20 秒。

        `core/uw.py` 专门为此定了 OHLC_TIMEOUT（至少 30 秒），
        这个仓库已经吃过一次「贴着超时、一抖动就整条失败」的亏。
        """
        with patch.object(quotes.uw, "available", return_value=True), \
             patch.object(quotes.uw, "get_many", return_value={}) as spy:
            quotes.us_quotes(["AAPL"])
        assert spy.call_args.kwargs.get("timeout") == quotes.uw.OHLC_TIMEOUT
        assert quotes.uw.OHLC_TIMEOUT >= 30.0

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
