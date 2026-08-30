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


def test_cn_regime_is_computed_from_daily_bars():
    out = regime.get("CN")
    assert out["market"] == "CN"
    assert out["source"] == "stock_ohlcv_daily"
    assert out["advances"] + out["declines"] > 100


def test_cn_regime_includes_limit_up_sentiment():
    out = regime.get("CN")
    assert "limit_up_count" in out


def test_regime_bad_market_raises():
    with pytest.raises(ValueError):
        regime.get("HK")


def test_freshness_reports_days_and_last_ingest():
    out = regime.freshness("US")
    assert out["latest_trading_day"] is not None
    assert isinstance(out["stale"], bool)
    assert isinstance(out["recent_jobs"], list)
