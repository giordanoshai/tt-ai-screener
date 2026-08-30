# -*- coding: utf-8 -*-
"""持仓分析。走真实数据（当前 6 只美股持仓）。"""
import json

from tools import review_positions as rp


def run(**kwargs):
    return json.loads(rp.review_positions(**kwargs))


def test_returns_every_position():
    """「每一只」要真的对得上。只判 len >= 1 的话，SQL 里混进一个
    limit 1、六只票丢五只，测试照样绿。"""
    from db import pg

    total = pg.query_one("select count(*) as n from user_positions")
    out = run()
    assert len(out["positions"]) == total["n"]
    first = out["positions"][0]
    assert {"ticker", "market", "currency", "shares", "avg_cost", "price",
            "pnl_pct", "pnl_amount", "trend", "stop", "earnings",
            "news", "flow", "flags", "urgency", "data_gaps"} <= set(first)


def test_summary_buckets_only_count_their_own_currency():
    """跨币种污染要真的测出来。现在只有美元持仓，所以「每个桶只装自己币种」
    这句话得靠跟明细实算比对来验，不能只判没有 total 键。"""
    out = run()
    assert "total" not in out["summary"]
    for currency, block in out["summary"].items():
        mine = [p for p in out["positions"] if p["currency"] == currency]
        assert block["count"] == len(mine)
        expected_cost = sum(p["avg_cost"] * p["shares"] for p in mine)
        assert block["cost_basis"] == round(expected_cost, 2)


def test_stop_breach_flag_matches_the_arithmetic():
    """breached 要跟现价和止损位的大小关系一致。只判键存在的话，
    把它写死成 True 或 False 都发现不了。"""
    out = run()
    checked = 0
    for pos in out["positions"]:
        stop = pos["stop"]
        if pos["price"] is None or stop["suggested_stop"] is None:
            continue
        assert stop["breached"] == (pos["price"] < stop["suggested_stop"])
        assert stop["basis"] in ("ma_20", "成本 - 2×ATR")
        if stop["forward_stop"] is not None:
            assert stop["forward_stop"] < pos["price"]
        checked += 1
    assert checked > 0, "一只票都没验到，这条测试等于没跑"


def test_urgency_order_is_not_just_alphabetical():
    """排序要真的按紧急度。所有票紧急度相同时，这条判据本身就失效了，
    所以先确认确实存在不同的紧急度，再验顺序。"""
    out = run()
    urgencies = [p["urgency"] for p in out["positions"]]
    assert urgencies == sorted(urgencies, reverse=True)
    assert len(set(urgencies)) > 1, (
        "所有持仓紧急度相同，排序无从验证——也说明紧急度分档没起作用")


def test_ticker_filter_narrows_to_one():
    everything = run()
    one = everything["positions"][0]["ticker"]
    out = run(ticker=one)
    assert [p["ticker"] for p in out["positions"]] == [one]


def test_unknown_ticker_returns_empty_not_error():
    out = run(ticker="NOSUCHTICKERXYZ")
    assert out["positions"] == []


def test_news_capped_at_three():
    out = run()
    assert all(len(p["news"]) <= 3 for p in out["positions"])


def test_flags_are_plain_chinese():
    """给用户看的是「跌破止损」，不是 stop_breached。"""
    out = run()
    flags = [f for p in out["positions"] for f in p["flags"]]
    assert all(isinstance(f, str) for f in flags)
    assert not any(f.isascii() for f in flags if f)


def test_returns_error_json_instead_of_raising():
    """「任何异常都返回 error JSON，不抛」是硬要求，但没有测试逼过这条路。
    这里把数据库查询打成必抛，看它是不是老实返回 error。"""
    from tools import review_positions as mod

    real = mod.pg.query

    def boom(*args, **kwargs):
        raise RuntimeError("数据库炸了")

    try:
        mod.pg.query = boom
        out = json.loads(mod.review_positions())
    finally:
        mod.pg.query = real
    assert "error" in out
    assert "数据库炸了" in out["error"]
