# -*- coding: utf-8 -*-
"""个股分析：一只票的完整体检。"""
import json

from tools import analyze_ticker as at


def run(ticker, **kwargs):
    return json.loads(at.analyze_ticker(ticker, **kwargs))


def test_us_ticker_full_checkup():
    out = run("NVDA")
    assert out["ticker"] == "NVDA"
    assert out["market"] == "US"
    assert {"trend", "hourly", "fundamental", "news", "flow",
            "gamma", "earnings", "insider", "congress", "setup", "plan",
            "data_gaps"} <= set(out)


def test_short_horizon_omits_fundamentals():
    """短波段不看基本面，字段就该是 None，而不是查了再不用。"""
    out = run("NVDA", horizon="short")
    assert out["fundamental"] is None


def test_mid_horizon_includes_fundamentals():
    out = run("NVDA", horizon="mid")
    assert out["fundamental"] is not None


def test_cn_ticker_works_and_says_what_is_missing():
    out = run("600519.SH")
    assert out["market"] == "CN"
    assert out["cn"] is not None
    gaps = " ".join(out["data_gaps"])
    assert "基本面" in gaps or "板块" in gaps


def test_hourly_is_none_when_ticker_has_no_hourly_bars():
    """小时线只采活跃池。没有就明说没有，不能编一个出来。"""
    out = run("NOSUCHTICKERXYZ")
    assert out["hourly"] is None


def test_unknown_ticker_returns_error_not_exception():
    out = run("NOSUCHTICKERXYZ")
    assert "error" in out or out["trend"] is None


def test_gamma_is_none_for_most_tickers():
    """uw_gex_daily 只有 41 只票，绝大多数是 None，这是已知的。"""
    out = run("MP")
    assert out["gamma"] is None or "call_wall" in out["gamma"]


def test_empty_ticker_errors():
    assert "error" in json.loads(at.analyze_ticker(""))


def test_hourly_setup_returns_dict_now():
    from tools import hourly
    out = hourly.hourly_setup("NVDA", bars=30)
    assert isinstance(out, dict)


def test_get_hourly_setup_still_returns_json_string():
    """网页面板和现有测试还在用这个包装，不能删。"""
    from tools import hourly
    assert isinstance(hourly.get_hourly_setup("NVDA", bars=30), str)
