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


def test_unknown_ticker_has_no_hourly_either():
    """假票连日线都没有，走的是最前面「没有行情数据」的提前返回，
    那条分支把 hourly 硬写成 None——测的是这条路，不是活跃池判断。"""
    out = run("NOSUCHTICKERXYZ")
    assert out["hourly"] is None


def test_hourly_is_none_for_a_ticker_outside_the_active_pool():
    """小时线只采活跃池。有日线但不在池子里的票，hourly 应该是 None
    并且在 data_gaps 里说明——原来那条用的假代码连日线都没有，
    走的是更早的提前返回，这段逻辑一次都没跑到。"""
    import pytest
    from db import pg

    row = pg.query_one("""
        select d.ticker
        from stock_ohlcv_daily d
        where d.market = 'US'
          and d.date = (select max(date) from stock_ohlcv_daily
                        where market = 'US')
          and not exists (select 1 from stock_ohlcv_min m
                          where m.ticker = d.ticker
                            and m.market = 'US'
                            and m.timeframe = '1h')
        limit 1
    """)
    if row is None:
        pytest.skip("所有有日线的美股都有小时线，测不出这条")
    out = run(row["ticker"])
    assert out["hourly"] is None
    assert any("小时线" in g for g in out["data_gaps"])


def test_mid_horizon_never_returns_a_short_tier_setup():
    """要中线就给中线。原来的写法先跑短线形态，命中就直接返回短线结论，
    mid 和 both 除了回显字段之外一模一样。"""
    out = run("NVDA", horizon="mid")
    assert out["setup"] is None or out["setup"] in ("trend_continuation",
                                                     "bottom_reversal")


def test_cn_data_gaps_do_not_repeat_the_same_missing_thing():
    """同一个缺失不能在清单里出现两遍——用户会以为是两个问题。"""
    out = run("600519.SH")
    board_gaps = [g for g in out["data_gaps"] if "板块" in g or "行业" in g]
    assert len(board_gaps) <= 1, f"板块缺失报了 {len(board_gaps)} 遍：{board_gaps}"


def test_low_score_plan_carries_a_confidence_caveat():
    """价位精确不等于这笔值得做。低分 + 缺口多的时候，计划旁边必须有话说清楚。"""
    out = run("600519.SH")
    if out.get("plan") and (out["score"] < 50 or len(out["data_gaps"]) >= 3):
        assert any("机械算出来" in r for r in out["risks"])


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
