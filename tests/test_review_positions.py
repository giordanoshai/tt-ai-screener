# -*- coding: utf-8 -*-
"""持仓分析。走真实数据（当前 6 只美股持仓）。"""
import json

from tools import review_positions as rp


def run(**kwargs):
    return json.loads(rp.review_positions(**kwargs))


def test_returns_every_position():
    out = run()
    assert len(out["positions"]) >= 1
    first = out["positions"][0]
    assert {"ticker", "market", "currency", "shares", "avg_cost", "price",
            "pnl_pct", "pnl_amount", "trend", "stop", "earnings",
            "news", "flow", "flags", "urgency", "data_gaps"} <= set(first)


def test_summary_is_per_currency_never_summed():
    """美元和人民币加在一起的数字没有任何意义。"""
    out = run()
    assert isinstance(out["summary"], dict)
    for currency, block in out["summary"].items():
        assert currency in ("USD", "CNY")
        assert {"market_value", "cost_basis", "pnl_amount", "pnl_pct",
                "count", "largest_position_pct"} <= set(block)
    assert "total" not in out["summary"]


def test_sorted_by_urgency_descending():
    out = run()
    urgencies = [p["urgency"] for p in out["positions"]]
    assert urgencies == sorted(urgencies, reverse=True)


def test_ticker_filter_narrows_to_one():
    everything = run()
    one = everything["positions"][0]["ticker"]
    out = run(ticker=one)
    assert [p["ticker"] for p in out["positions"]] == [one]


def test_unknown_ticker_returns_empty_not_error():
    out = run(ticker="NOSUCHTICKERXYZ")
    assert out["positions"] == []


def test_stop_has_basis_and_breach_flag():
    out = run()
    for pos in out["positions"]:
        assert {"suggested_stop", "basis", "distance_pct", "breached"} <= set(pos["stop"])


def test_news_capped_at_three():
    out = run()
    assert all(len(p["news"]) <= 3 for p in out["positions"])


def test_flags_are_plain_chinese():
    """给用户看的是「跌破止损」，不是 stop_breached。"""
    out = run()
    flags = [f for p in out["positions"] for f in p["flags"]]
    assert all(isinstance(f, str) for f in flags)
    assert not any(f.isascii() for f in flags if f)


def test_never_raises_and_always_returns_json():
    assert isinstance(rp.review_positions(), str)
