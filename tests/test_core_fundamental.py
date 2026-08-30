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
    """stock_fundamentals 一只票几十行，必须只回来一行。"""
    out = fundamental.get(["NVDA"], "US")
    assert isinstance(out["NVDA"], dict)


def test_get_unknown_ticker_is_none():
    out = fundamental.get(["NOSUCHTICKERXYZ"], "US")
    assert out["NOSUCHTICKERXYZ"] is None


def test_get_cn_mostly_empty_but_never_raises():
    """A股基本面只有 150 只票，而且只有营收增长率有值。"""
    out = fundamental.get(["600519.SH", "000001.SZ"], "CN")
    assert set(out) == {"600519.SH", "000001.SZ"}


def test_get_empty_list():
    assert fundamental.get([], "US") == {}
