# -*- coding: utf-8 -*-
"""基本面取数。只被中线档调用。"""
from core import fundamental


def test_get_returns_valuation_and_growth():
    out = fundamental.get(["NVDA", "AAPL"], "US")
    assert set(out) == {"NVDA", "AAPL"}
    row = out["NVDA"]
    assert row is not None
    assert {"pe_ratio", "revenue_growth_yoy", "profit_margin",
            "analyst_target_price", "week_52_high", "market_cap"} <= set(row)


def test_get_dedupes_to_the_newest_row():
    """一只票在基本面表里有几十行，必须回最新那一行。

    不能只断言「回来的是一个字典」——`get()` 里的字典推导本来就会把同一个
    ticker 的多行压成一条，把 SQL 里的 distinct on 整个删掉那种断言照样绿。
    要证明的是「拿到的是最新那行」，所以直接跟表里的 max(updated_at) 比。
    """
    from db import pg

    total = pg.query_one(
        "select count(*) as n from stock_fundamentals "
        "where ticker = %s and market = %s", ("NVDA", "US"))
    assert total["n"] > 1, "NVDA 在基本面表里只有一行，这条测试证明不了去重"

    newest = pg.query_one(
        "select max(updated_at) as newest from stock_fundamentals "
        "where ticker = %s and market = %s", ("NVDA", "US"))

    out = fundamental.get(["NVDA"], "US")
    assert out["NVDA"] is not None
    assert out["NVDA"]["updated_at"] == newest["newest"]


def test_get_unknown_ticker_is_none():
    out = fundamental.get(["NOSUCHTICKERXYZ"], "US")
    assert out["NOSUCHTICKERXYZ"] is None


def test_get_cn_mostly_empty_but_never_raises():
    """A股基本面只有 150 只票，而且只有营收增长率有值。"""
    out = fundamental.get(["600519.SH", "000001.SZ"], "CN")
    assert set(out) == {"600519.SH", "000001.SZ"}


def test_get_empty_list():
    assert fundamental.get([], "US") == {}
