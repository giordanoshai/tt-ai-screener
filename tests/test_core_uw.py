# -*- coding: utf-8 -*-
"""UW 实时客户端。纯函数部分离线测，联网部分只做冒烟。"""
import urllib.error

import pytest

import config
from core import uw


# ---------------- 纯函数：期权溢价怎么算方向 ----------------

def test_split_premium_call_ask_is_bullish():
    """在卖价买入看涨期权 = 有人主动看多。"""
    assert uw.split_premium("call", 100.0, 30.0) == (100.0, 30.0)


def test_split_premium_put_is_mirrored():
    """在卖价买入看跌期权 = 看空；在买价卖出看跌 = 看多。"""
    assert uw.split_premium("put", 100.0, 30.0) == (30.0, 100.0)


def test_split_premium_unknown_type_is_neutral():
    assert uw.split_premium(None, 100.0, 30.0) == (0.0, 0.0)


# ---------------- 纯函数：从警报里认出标的 ----------------

def test_underlying_prefers_meta():
    alert = {"symbol": "CIFR261120C00019000",
             "meta": {"underlying_symbol": "CIFR"}}
    assert uw.underlying_of(alert) == "CIFR"


def test_underlying_falls_back_to_occ_prefix():
    """meta 里没有时，从期权合约代码前缀切出股票代码。"""
    assert uw.underlying_of({"symbol": "NVDA261002P00190000"}) == "NVDA"


def test_underlying_of_noise_alert_is_none():
    """potus_schedule 这类警报没有 symbol，不该崩也不该瞎猜。"""
    assert uw.underlying_of({"noti_type": "potus_schedule",
                             "symbol": None, "meta": {}}) is None


# ---------------- 纯函数：一只票的期权流汇总 ----------------

def test_summarize_flow_nets_bullish_against_bearish():
    rows = [
        {"type": "call", "total_ask_side_prem": "1000",
         "total_bid_side_prem": "0", "total_premium": "1000"},
        {"type": "put", "total_ask_side_prem": "400",
         "total_bid_side_prem": "0", "total_premium": "400"},
    ]
    out = uw.summarize_flow(rows)
    assert out["bullish_premium"] == 1000.0
    assert out["bearish_premium"] == 400.0
    assert out["net_premium"] == 600.0
    assert out["alert_count"] == 2
    assert out["biggest_premium"] == 1000.0


def test_summarize_flow_empty_is_zeroed_not_none():
    out = uw.summarize_flow([])
    assert out["alert_count"] == 0
    assert out["net_premium"] == 0.0


# ---------------- 联网：失败要降级，不能抛 ----------------

def test_get_bad_path_returns_none_not_raise():
    """外部接口出任何问题都返回 None，让调用方走降级，不是让整个工具挂掉。"""
    assert uw.get("/api/definitely-not-a-real-path-xyz") is None


@pytest.mark.skipif(not uw.available(), reason="没配 UW_T_KEY")
def test_flow_alerts_smoke(live_uw):
    """真联网的冒烟。`live_uw` 解除 conftest 的默认拦截，见那边的注释。"""
    rows = uw.flow_alerts("NVDA", limit=3)
    assert rows is None or isinstance(rows, list)
    if rows:
        assert "total_premium" in rows[0]


# ---------------- 纯函数：1h K 线怎么解析 ----------------
#
# 2026-09-03 起美股小时线也走实时（上游把 us_hourly 改成了按需，库里那份
# 不再每天更新）。下面这批样本的形状是当天拿 AAPL 实测抄回来的：价格全是
# 字符串、volume 是数字、`market_time` 分 r / pr / po 三档、响应本身是倒序。

def _raw_bar(start="2026-09-02T14:30:00Z", market_time="r", **over):
    """一根 UW 原样返回的 K 线。"""
    bar = {"open": "100.5", "high": "101.0", "low": "100.0", "close": "100.8",
           "volume": 12345, "total_volume": 33776366,
           "start_time": start, "end_time": "2026-09-02T15:30:00Z",
           "market_time": market_time}
    bar.update(over)
    return bar


def test_parse_1h_keeps_only_the_regular_session():
    """盘前盘后必须丢掉。

    实测 AAPL 的 3M 档 1040 行里盘中只占 390 行——盘前盘后照单全收的话，
    「最近 60 根」有一半是夜里那几根几千手的稀量线，均线和量比全被带偏。
    库里存的也只有盘中这一档，两条路口径必须一致。
    """
    rows = uw.parse_ohlc_1h([
        _raw_bar("2026-09-02T12:00:00Z", "pr"),
        _raw_bar("2026-09-02T14:30:00Z", "r"),
        _raw_bar("2026-09-02T21:00:00Z", "po"),
    ])
    assert len(rows) == 1
    assert rows[0]["ts"].hour == 14


def test_parse_1h_turns_string_prices_into_floats():
    row = uw.parse_ohlc_1h([_raw_bar()])[0]
    for name in ("open", "high", "low", "close", "volume"):
        assert isinstance(row[name], float), name
    assert row["close"] == 100.8


def test_parse_1h_row_shape_matches_the_database_query():
    """形状必须跟 `tools/hourly.py::HOURLY_SQL` 一样。

    对不齐的话调用方就得为「实时」和「库里」写两套解析——
    A股那条路 2026-09-01 就是按这个约定接上去的。
    """
    row = uw.parse_ohlc_1h([_raw_bar()])[0]
    assert {"ts", "ts_local", "session", "market",
            "open", "high", "low", "close", "volume"} <= set(row)
    assert row["session"] == "RTH"
    assert row["market"] == "US"


def test_parse_1h_sorts_oldest_first():
    """UW 返回的是倒序（最新在前），调用方要的是正序。"""
    rows = uw.parse_ohlc_1h([
        _raw_bar("2026-09-02T16:30:00Z"),
        _raw_bar("2026-09-02T14:30:00Z"),
        _raw_bar("2026-09-02T15:30:00Z"),
    ])
    assert [r["ts"].hour for r in rows] == [14, 15, 16]


def test_parse_1h_ts_is_the_bar_start_in_utc():
    """`start_time` 本来就是这根 K 线【开始】的时刻，不用像 A股那样减一根。"""
    row = uw.parse_ohlc_1h([_raw_bar("2026-09-02T14:30:00Z")])[0]
    assert row["ts"].tzinfo is not None
    assert row["ts"].utcoffset().total_seconds() == 0
    assert (row["ts"].year, row["ts"].month, row["ts"].day) == (2026, 9, 2)


def test_parse_1h_ts_local_is_new_york_wall_clock():
    """库里那列是 `ts at time zone 'America/New_York'`，出来不带时区。

    14:30 UTC 是纽约夏令时的 10:30 —— 盘中第二根。
    """
    row = uw.parse_ohlc_1h([_raw_bar("2026-09-02T14:30:00Z")])[0]
    if row["ts_local"] is None:
        pytest.skip("这台机器没装 tzdata")
    assert row["ts_local"].tzinfo is None
    assert (row["ts_local"].hour, row["ts_local"].minute) == (10, 30)


def test_parse_1h_drops_bars_with_a_missing_price():
    """四个价格缺一个就整根丢掉——不能拿 None 去算均线。"""
    assert uw.parse_ohlc_1h([_raw_bar(high=None)]) == []
    assert uw.parse_ohlc_1h([_raw_bar(close="")]) == []


def test_parse_1h_drops_nan_prices():
    """NaN 既不是假值也不满足 <= 0，不显式挡掉会一路算进均线。"""
    assert uw.parse_ohlc_1h([_raw_bar(low="nan")]) == []


def test_parse_1h_dedupes_by_timestamp():
    rows = uw.parse_ohlc_1h([
        _raw_bar("2026-09-02T14:30:00Z", close="100.0"),
        _raw_bar("2026-09-02T14:30:00Z", close="200.0"),
    ])
    assert len(rows) == 1
    assert rows[0]["close"] == 200.0   # 重复的留最后一条


def test_parse_1h_survives_garbage():
    """接口哪天返回了别的东西，不能把整只票的体检干掉。"""
    assert uw.parse_ohlc_1h(None) == []
    assert uw.parse_ohlc_1h([]) == []
    assert uw.parse_ohlc_1h(["not a dict", {}, _raw_bar(start_time="昨天")]) == []


# ---------------- 小时线：窗口、指数代码、降级 ----------------

def test_ohlc_window_stays_at_3m():
    """★ 回归：别改回 1M ★

    实测 AAPL 的 1M 档只有 138 根盘中线，而 `tools/hourly.py` 最多要 200 根，
    那条 200 小时均线会永远算不出来。而且换小窗口也省不到时间——
    1M 档 12.9 秒、3M 档 14.0 秒，耗时几乎全是代理那边的固定开销。
    """
    assert uw.OHLC_WINDOW == "3M"


def test_hourly_refuses_index_symbols_without_calling_the_api(monkeypatch):
    """`^VIX` 这类指数代码喂给 UW 的【股票】接口一律 422。

    明知要失败还去等十几秒，只是白白拖慢工具。tt-data-hub 2026-09-03
    也是在源头把它挡出活跃池的。
    """
    called = []
    monkeypatch.setattr(uw, "get", lambda *a, **k: called.append(a) or [])
    assert uw.hourly("^VIX") == []
    assert uw.hourly("") == []
    assert not called, "指数代码不该发请求"


def test_hourly_returns_empty_when_the_api_gives_nothing(monkeypatch):
    """取不到就是空列表，不抛——调用方据此退回数据库。"""
    monkeypatch.setattr(uw, "get", lambda *a, **k: None)
    assert uw.hourly("AAPL") == []


def test_hourly_keeps_only_the_last_n_bars(monkeypatch):
    """接口一次给整个 3M 窗口，这里只要最近那几根，而且要最新的那几根。"""
    payload = [_raw_bar(f"2026-09-02T{hour:02d}:30:00Z", close=str(100 + hour))
               for hour in range(10, 20)]
    monkeypatch.setattr(uw, "get", lambda *a, **k: payload)
    rows = uw.hourly("AAPL", bars=3)
    assert len(rows) == 3
    assert [r["ts"].hour for r in rows] == [17, 18, 19]


def test_hourly_without_a_token_does_not_call_the_api(monkeypatch):
    """没配密钥就别调，直接让调用方走库里那条路。"""
    monkeypatch.setattr(uw, "available", lambda: False)
    monkeypatch.setattr(uw, "get", lambda *a, **k: pytest.fail("不该发请求"))
    assert uw.hourly("AAPL") == []


# ---------------- 出网走哪条路 ----------------

@pytest.fixture
def spy_fetch(monkeypatch):
    """把真正发请求那层换成录音机，只记下「这一趟打算走哪个代理」。"""
    seen = {}

    def fake(request, timeout, proxy):
        seen["proxy"] = proxy
        seen["timeout"] = timeout
        return {"data": [{"ok": 1}]}

    monkeypatch.setattr(uw, "_fetch", fake)
    monkeypatch.setattr(uw, "available", lambda: True)
    return seen


class TestProxy:
    """UW 的请求要出国。原来这一路完全没有代理配置，靠机器上碰巧存在的
    HTTP_PROXY 环境变量在走——换台机器（服务器上的 systemd、Claude Desktop
    拉起来的 MCP 子进程）就可能悄悄变成直连，而 get() 吞掉一切异常，
    表现只是「期权数据怎么全是空的」。所以配置要显式，配错要出声。"""

    def test_configured_proxy_reaches_the_request(self, monkeypatch, spy_fetch):
        monkeypatch.setattr(config, "UW_PROXY", "http://127.0.0.1:1081")
        assert uw.get("/api/whatever") == [{"ok": 1}]
        assert spy_fetch["proxy"] == "http://127.0.0.1:1081"

    def test_bare_address_gets_a_scheme(self, monkeypatch, spy_fetch):
        """漏写 http:// 是最常见的配错，补上而不是让它静默失效。"""
        monkeypatch.setattr(config, "UW_PROXY", "127.0.0.1:1081")
        uw.get("/api/whatever")
        assert spy_fetch["proxy"] == "http://127.0.0.1:1081"

    def test_unset_means_follow_the_environment(self, monkeypatch, spy_fetch):
        """没配就是原来的行为：跟随系统的 HTTP_PROXY，不强行改成直连。"""
        monkeypatch.setattr(config, "UW_PROXY", "")
        uw.get("/api/whatever")
        assert spy_fetch["proxy"] == ""

    def test_socks_proxy_fails_loudly_instead_of_silently(
            self, monkeypatch, capsys):
        """标准库不支持 SOCKS。不能假装直连——那会超时十几秒然后返回
        None，看日志根本不知道该改什么。"""
        monkeypatch.setattr(config, "UW_PROXY", "socks5://127.0.0.1:1080")
        monkeypatch.setattr(uw, "available", lambda: True)
        monkeypatch.setattr(uw, "_fetch",
                            lambda *a, **k: pytest.fail("配错了还发什么请求"))
        uw.reset_warnings()

        assert uw.get("/api/whatever") is None

        warned = capsys.readouterr().err
        assert "SOCKS" in warned and "UW_PROXY" in warned

    def test_the_same_misconfiguration_only_warns_once(
            self, monkeypatch, capsys):
        """一轮盯盘要调几十次接口，同一个配错不该刷几十行。"""
        monkeypatch.setattr(config, "UW_PROXY", "socks5://127.0.0.1:1080")
        monkeypatch.setattr(uw, "available", lambda: True)
        uw.reset_warnings()

        for _ in range(5):
            uw.get("/api/whatever")

        assert capsys.readouterr().err.count("SOCKS") == 1


class TestFailuresAreVisible:

    def test_a_dead_request_says_so_on_stderr(self, monkeypatch, capsys):
        """取不到还是返回 None（调用方的降级契约不变），但要留下痕迹，
        不然「代理掉了」和「这只票没有期权数据」长得一模一样。"""
        monkeypatch.setattr(config, "UW_PROXY", "http://127.0.0.1:1081")
        monkeypatch.setattr(uw, "available", lambda: True)

        def boom(*a, **k):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(uw, "_fetch", boom)
        uw.reset_warnings()

        assert uw.get("/api/whatever") is None

        warned = capsys.readouterr().err
        assert "127.0.0.1:1081" in warned
