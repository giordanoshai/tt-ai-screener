# -*- coding: utf-8 -*-
"""UW 实时客户端。纯函数部分离线测，联网部分只做冒烟。"""
import pytest

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
def test_flow_alerts_smoke():
    rows = uw.flow_alerts("NVDA", limit=3)
    assert rows is None or isinstance(rows, list)
    if rows:
        assert "total_premium" in rows[0]
