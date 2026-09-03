# -*- coding: utf-8 -*-
"""core/options.py 的测试 —— 期权贵不贵、量比、有没有人在建新仓。

这个模块是 2026-09-03 补的，补的是一个「有数据没工具」的缺口：
`tt-data-hub` 每天往四张表里写期权和资金面数据，本仓库此前一行都没读过。

**量级是这里最要紧的事。** 同一份 payload 里三种量级混着放：

    iv_rank        0~100 的排名   7.2952  直接用
    iv30d          小数           0.327   要乘 100 才是 32.7%
    implied_move   **美元金额**   14.301  NVDA 股价 224，这是 6.4% 不是 14.3%
    oi_change      **倍数**       2.9857  涨到原来的 2.99 倍，不是涨 2.99%

这个坑在本仓库已经炸过三次（见 CLAUDE.md「比率字段的单位约定」），
所以下面每一种量级都有一条专门盯着的用例。
"""
import json
from unittest.mock import patch

import pytest

from core import options


# ---------------- 期权合约代码怎么拆 ----------------

def test_parse_occ_reads_expiry_type_and_strike():
    """`NVDA270115P00100000` = 2027-01-15 到期、看跌、行权价 100。"""
    assert options.parse_occ("NVDA270115P00100000") == {
        "expiry": "2027-01-15", "type": "put", "strike": 100.0}


def test_parse_occ_handles_calls_and_fractional_strikes():
    """行权价那 8 位是千分位：`00222500` = 222.5。"""
    out = options.parse_occ("NVDA260902C00222500")
    assert out["type"] == "call"
    assert out["strike"] == 222.5


def test_parse_occ_returns_none_on_garbage():
    """代码格式变了不该把整只票的分析干掉。"""
    for bad in ("", None, "AAPL", "NVDA991399X00100000", "NVDA2701159900100000"):
        assert options.parse_occ(bad) is None


# ---------------- 期权贵不贵 ----------------

def _screener_row(ticker="NVDA", **over):
    payload = {"iv_rank": "7.2952", "iv30d": "0.327000",
               "iv30d_1w": "0.412", "iv30d_1m": "0.442",
               "implied_move": "14.301000", "close": "224.41",
               "gex_ratio": "0.53431518509027112495", "sector": "Technology"}
    payload.update(over)
    return {"ticker": ticker, "snapshot_date": "2026-09-03", "payload": payload}


def test_iv_rank_is_used_as_is():
    """0~100 的排名，别再乘 100。"""
    with patch.object(options.pg, "query", return_value=[_screener_row()]):
        out = options.iv_snapshot(["NVDA"])
    assert out["NVDA"]["iv_rank"] == 7.3


def test_iv30d_is_converted_from_decimal_to_percent():
    """★ 库里是小数 0.327，交出去必须是 32.7 ★

    不换的话，`analyze_ticker` 里会并排出现「隐含波动率 0.327」和
    「ATR 3.4%」两个差 100 倍的数字，看的人没法比。
    """
    with patch.object(options.pg, "query", return_value=[_screener_row()]):
        out = options.iv_snapshot(["NVDA"])
    assert out["NVDA"]["iv30d_pct"] == 32.7
    assert out["NVDA"]["iv30d_1w_ago_pct"] == 41.2
    assert out["NVDA"]["iv30d_1m_ago_pct"] == 44.2


def test_implied_move_is_dollars_and_gets_a_percent_alongside():
    """★ `implied_move` 是美元金额，不是百分比 ★

    NVDA 收 224.41、implied_move 14.301——把它当百分比读就成了「财报要动
    14%」，实际是 6.37%，差一倍多。这条钉住换算方向。
    """
    with patch.object(options.pg, "query", return_value=[_screener_row()]):
        out = options.iv_snapshot(["NVDA"])
    assert out["NVDA"]["implied_move"] == 14.3
    assert out["NVDA"]["implied_move_pct"] == 6.37


def test_iv_snapshot_survives_a_broken_payload():
    rows = [_screener_row(), {"ticker": "X", "snapshot_date": "2026-09-03",
                              "payload": "not json"}]
    with patch.object(options.pg, "query", return_value=rows):
        out = options.iv_snapshot(["NVDA", "X"])
    assert "NVDA" in out and "X" not in out


def test_iv_snapshot_with_no_tickers_does_not_query():
    with patch.object(options.pg, "query") as query:
        assert options.iv_snapshot([]) == {}
        assert options.iv_snapshot(None) == {}
    query.assert_not_called()


# ---------------- 期权量和未平仓量 ----------------

def _volume_row(ticker="NVDA", **over):
    payload = {"call_volume": 3603522, "put_volume": 2187213,
               "call_open_interest": 8191680, "put_open_interest": 7205986,
               "avg_30_day_call_volume": "2000000",
               "avg_30_day_put_volume": "1555000",
               "net_call_premium": "54564360.00",
               "net_put_premium": "-9655481.00"}
    payload.update(over)
    return {"ticker": ticker, "snapshot_date": "2026-09-03", "payload": payload}


def test_put_call_ratio_and_volume_vs_30d():
    """量比是「今天的总量 / 30 日均量」，看涨看跌两边都要算进去。"""
    with patch.object(options.pg, "query", return_value=[_volume_row()]):
        out = options.flow_volume(["NVDA"])["NVDA"]
    assert out["put_call_ratio"] == 0.61
    # (3603522 + 2187213) / (2000000 + 1555000) = 1.63
    assert out["volume_vs_30d"] == 1.63


def test_volume_vs_30d_is_none_when_there_is_no_average():
    """没有均量就说没有，不要拿 0 除或者拿 0 冒充。"""
    with patch.object(options.pg, "query", return_value=[
            _volume_row(avg_30_day_call_volume=None,
                        avg_30_day_put_volume=None)]):
        out = options.flow_volume(["NVDA"])["NVDA"]
    assert out["volume_vs_30d"] is None


# ---------------- 谁在建新仓 ----------------

def _oi_payload():
    return [
        {"option_symbol": "NVDA270115P00100000", "oi_diff_plain": 138840,
         "curr_oi": 185341, "last_oi": 46501, "oi_change": "2.9857",
         "volume": 150068, "days_of_oi_increases": 1},
        {"option_symbol": "NVDA260902C00222500", "oi_diff_plain": 14418,
         "curr_oi": 26057, "oi_change": "1.24", "volume": 87398,
         "days_of_oi_increases": 3},
        {"option_symbol": "NVDA270115P00150000", "oi_diff_plain": 93455,
         "curr_oi": 135599, "oi_change": "2.22", "volume": 100160,
         "days_of_oi_increases": 1},
        # 未平仓减少的：那是平仓了结，不是建新仓，必须排除
        {"option_symbol": "NVDA260905C00230000", "oi_diff_plain": -50000,
         "curr_oi": 1000, "oi_change": "0.2", "volume": 400},
    ]


def _oi_row():
    return {"ticker": "NVDA", "snapshot_date": "2026-09-03",
            "payload": _oi_payload()}


def test_oi_builds_sorts_by_how_many_contracts_were_added():
    with patch.object(options.pg, "query_one", return_value=_oi_row()):
        out = options.oi_builds("NVDA")
    assert [b["oi_added"] for b in out] == [138840, 93455, 14418]


def test_oi_builds_drops_positions_being_closed():
    """★ 未平仓【减少】的合约不算「有人在建新仓」★

    这正是这个模块存在的理由：成交量大可能只是原来的人在平仓了结。
    把减少的也算进来，等于把「有人跑路」读成「有人押注」。
    """
    with patch.object(options.pg, "query_one", return_value=_oi_row()):
        out = options.oi_builds("NVDA", top=10)
    assert all(b["oi_added"] > 0 for b in out)
    assert len(out) == 3


def test_oi_builds_decodes_the_contract():
    with patch.object(options.pg, "query_one", return_value=_oi_row()):
        top = options.oi_builds("NVDA")[0]
    assert top["expiry"] == "2027-01-15"
    assert top["type"] == "put"
    assert top["strike"] == 100.0


def test_oi_multiple_is_a_multiple_not_a_percent():
    """★ 2.99 = 涨到原来的 2.99 倍，不是涨 2.99% ★"""
    with patch.object(options.pg, "query_one", return_value=_oi_row()):
        top = options.oi_builds("NVDA")[0]
    assert top["oi_multiple"] == 2.99
    assert top["oi_now"] == 185341


def test_oi_builds_caps_the_list():
    """一次给 50 个合约，原样丢出去是 6000 多字符的原始表。"""
    with patch.object(options.pg, "query_one", return_value=_oi_row()):
        assert len(options.oi_builds("NVDA", top=1)) == 1


def test_oi_builds_is_empty_when_there_is_no_row():
    with patch.object(options.pg, "query_one", return_value=None):
        assert options.oi_builds("NVDA") == []
    assert options.oi_builds("") == []


# ---------------- 全市场内部人 ----------------

def _ibs_row():
    return {"snapshot_date": "2026-09-03", "payload": [
        {"sells": 127, "purchases": 21, "filing_date": "2026-09-02",
         "sells_notional": "-262893636.77", "purchases_notional": "3604639.44"},
        {"sells": 484, "purchases": 51, "filing_date": "2026-09-01",
         "sells_notional": "-997651880.21", "purchases_notional": "127786615.26"},
    ]}


def test_insider_tide_sums_the_window():
    with patch.object(options.pg, "query_one", return_value=_ibs_row()):
        out = options.insider_tide(days=2)
    assert out["days"] == 2
    assert out["sell_count"] == 611
    assert out["buy_count"] == 72
    assert out["to"] == "2026-09-02"
    assert out["from"] == "2026-09-01"


def test_insider_tide_takes_sells_as_a_positive_amount():
    """★ 库里 `sells_notional` 是负数 ★

    不取绝对值的话，「卖出金额」会是 −12.6 亿，而下面那个比值会变成负数——
    读的人会以为高管在净买入。
    """
    with patch.object(options.pg, "query_one", return_value=_ibs_row()):
        out = options.insider_tide(days=2)
    assert out["sell_amount"] > 0
    assert out["sell_over_buy"] > 0


def test_insider_tide_is_none_when_there_is_nothing():
    with patch.object(options.pg, "query_one", return_value=None):
        assert options.insider_tide() is None
    with patch.object(options.pg, "query_one",
                      return_value={"snapshot_date": "x", "payload": []}):
        assert options.insider_tide() is None


# ---------------- 冲着真库跑一次 ----------------

class TestAgainstTheRealDatabase:
    """这几张表每天都在写，所以真库这一路应该是通的。

    通不了要么是上游那几个任务挂了，要么是列名改了——两种都该被看见。
    """

    def test_iv_snapshot_reads_real_rows(self):
        out = options.iv_snapshot(["NVDA", "AAPL", "TSLA"])
        if not out:
            pytest.skip("uw_screener_daily 里这几只都没有")
        one = next(iter(out.values()))
        assert one["iv_rank"] is None or 0 <= one["iv_rank"] <= 100

    def test_flow_volume_reads_real_rows(self):
        out = options.flow_volume(["NVDA", "AAPL", "TSLA"])
        if not out:
            pytest.skip("uw_options_volume_daily 里这几只都没有")
        assert all("volume_vs_30d" in v for v in out.values())

    def test_cn_tickers_come_back_empty(self):
        """UW 不做 A股，这四张表里没有一行 CN——空是对的，不是故障。"""
        assert options.iv_snapshot(["600519.SH"]) == {}
        assert options.flow_volume(["600519.SH"]) == {}

    def test_everything_is_json_serializable(self):
        """要交给 MCP 序列化，Decimal 和 date 会当场炸。"""
        json.dumps({
            "iv": options.iv_snapshot(["NVDA"]),
            "vol": options.flow_volume(["NVDA"]),
            "oi": options.oi_builds("NVDA"),
            "tide": options.insider_tide(),
        })
