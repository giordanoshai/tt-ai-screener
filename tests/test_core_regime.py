# -*- coding: utf-8 -*-
"""市场环境。美股读现成的宽度表，A股当场算。"""
import datetime

import pytest

from core import regime


def test_us_regime_reads_breadth_table():
    out = regime.get("US")
    assert out["market"] == "US"
    assert isinstance(out["date"], datetime.date)
    assert out["source"] == "market_breadth_daily"
    assert 0 <= out["pct_above_ma20"] <= 100


def test_us_regime_says_it_is_sp500_only():
    """market_breadth_daily 是标普 500 口径，不是全市场，得说清楚。"""
    out = regime.get("US")
    assert "标普" in out["note"] or "500" in out["note"]


def test_cn_regime_counts_only_cn_tickers():
    """市场过滤写错时必须红。

    只断言「涨跌家数 > 100」是拦不住的——美股口径、混合口径也都远超 100，
    而那正是规矩 2 要防的那类事故。所以直接跟库里 A股当日的真实票数比。
    """
    from db import pg

    from core import snapshot as snap_mod

    day = snap_mod.latest_trading_day("CN")
    expected = pg.query_one(
        "select count(*) as n from stock_ohlcv_daily "
        "where market = 'CN' and date = %s", (day,))
    out = regime.get("CN")
    assert out["source"] == "stock_ohlcv_daily"
    assert out["date"] == day
    assert out["total"] == expected["n"]
    assert out["advances"] + out["declines"] <= out["total"]


def test_cn_regime_limit_up_count_matches_the_table():
    """涨停家数要跟涨停表对得上，不能只判键在不在。"""
    from db import pg

    row = pg.query_one(
        "select count(*) as n from cn_limit_pool_daily "
        "where snapshot_date = (select max(snapshot_date) from cn_limit_pool_daily)")
    out = regime.get("CN")
    assert out["limit_up_count"] == row["n"]


def test_regime_bad_market_raises():
    with pytest.raises(ValueError):
        regime.get("HK")


def test_freshness_days_since_matches_the_calendar():
    """陈旧判断要真的算过，不能只判它返回了个 bool。"""
    import datetime

    from core import snapshot as snap_mod

    out = regime.freshness("US")
    day = snap_mod.latest_trading_day("US")
    assert out["market"] == "US"
    assert out["latest_trading_day"] == day
    assert out["days_since"] == (datetime.date.today() - day).days
    assert out["stale"] == (out["days_since"] > snap_mod.STALE_DAYS)
    assert isinstance(out["failed_jobs"], list)
    assert isinstance(out["partial_jobs"], list)


def test_freshness_separates_failed_from_partial():
    """partial 是「跑完了但某个可选源没取到」，不是失败。混在一起报警，
    用户会学会无视这个警报，然后真出事那次也一起无视。"""
    out = regime.freshness("US")
    assert not (set(out["failed_jobs"]) & set(out["partial_jobs"]))
    for name in out["failed_jobs"]:
        assert "(failed)" in name
    for name in out["partial_jobs"]:
        assert "(partial)" in name


def test_freshness_only_reports_jobs_from_that_market():
    """按市场过滤要真的生效。不过滤的话，A股任务会顶着「US」的标签报给用户。"""
    us = regime.freshness("US")
    cn = regime.freshness("CN")
    assert not any(name.startswith("cn_") for name
                   in us["failed_jobs"] + us["partial_jobs"])
    assert not any(name.startswith("us_") for name
                   in cn["failed_jobs"] + cn["partial_jobs"])
