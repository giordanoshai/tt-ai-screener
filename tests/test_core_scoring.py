# -*- coding: utf-8 -*-
"""形态识别和评分。纯函数，全部离线测。"""
import inspect

import pytest

from core import scoring


def snap(**overrides):
    """一份「什么都正常」的快照，各测试只改自己关心的字段。"""
    base = {
        "ticker": "TEST", "market": "US", "close": 100.0, "open": 98.0,
        "ma_20": 95.0, "ma_50": 90.0, "ma_200": 80.0,
        "above_ma20": True, "above_ma50": True, "above_ma200": True,
        "rsi_14": 55.0, "vol_ratio": 1.8, "atr_14": 4.0, "atr_pct": 4.0,
        "high_20": 99.0, "high_55": 99.0, "prior_high_20": 99.0,
        "dist_ma20_pct": 5.3, "dist_ma50_pct": 11.1,
        "dist_from_20d_high_pct": 1.0, "ma_50_slope_pct": 1.2,
        "had_vol_up_3d": True, "made_20d_high_recently": True,
        "dollar_volume": 5e8, "stale": False, "days_since_bar": 1,
    }
    base.update(overrides)
    return base


# ---------------- 硬约束：短线评分不许碰基本面 ----------------

def test_score_short_signature_has_no_fundamental_param():
    """设计文档第三节：写错了要直接报错，不能悄悄用上基本面。"""
    params = set(inspect.signature(scoring.score_short).parameters)
    assert params == {"snap", "flow", "news"}


def test_score_short_cn_signature_has_no_fundamental_param():
    params = set(inspect.signature(scoring.score_short_cn).parameters)
    assert "fund" not in params
    assert "fundamental" not in params


# ---------------- 形态识别 ----------------

def test_breakout_needs_all_three_conditions():
    assert scoring.detect_short_setup(snap()) == "breakout"


def test_breakout_rejected_without_volume():
    """量比不够就不是突破，是假突破。"""
    out = scoring.detect_short_setup(snap(vol_ratio=1.1, dist_ma20_pct=5.3))
    assert out != "breakout"


def test_breakout_rejected_when_below_prior_high():
    out = scoring.detect_short_setup(snap(close=98.0, prior_high_20=105.0,
                                          dist_ma20_pct=3.2))
    assert out != "breakout"


def test_pullback_detected_at_ma20():
    out = scoring.detect_short_setup(
        snap(close=95.5, prior_high_20=105.0, vol_ratio=0.9,
             dist_ma20_pct=0.5, rsi_14=50.0))
    assert out == "pullback"


def test_pullback_rejected_when_rsi_out_of_band():
    out = scoring.detect_short_setup(
        snap(close=95.5, prior_high_20=105.0, vol_ratio=0.9,
             dist_ma20_pct=0.5, rsi_14=75.0))
    assert out is None


def test_oversold_bounce_detected():
    out = scoring.detect_short_setup(
        snap(close=82.0, ma_20=88.0, ma_50=95.0, ma_200=80.0,
             above_ma20=False, above_ma50=False, prior_high_20=110.0,
             rsi_14=28.0, dist_ma20_pct=-6.8, dist_ma50_pct=-13.7,
             vol_ratio=1.3, had_vol_up_3d=True))
    assert out == "oversold_bounce"


def test_no_setup_returns_none():
    out = scoring.detect_short_setup(
        snap(close=91.0, prior_high_20=120.0, vol_ratio=0.7,
             rsi_14=38.0, dist_ma20_pct=-4.2, dist_ma50_pct=1.1,
             above_ma200=True))
    assert out is None


def test_detect_short_setup_survives_missing_fields():
    """停牌的票可能只有收盘价，什么都缺，不许抛。"""
    assert scoring.detect_short_setup({"close": 10.0}) is None


def test_mid_setup_trend_continuation_needs_fundamentals():
    fund = {"revenue_growth_yoy": 12.0, "profit_margin": 8.0,
            "week_52_high": 105.0}
    assert scoring.detect_mid_setup(snap(), fund) == "trend_continuation"


def test_mid_setup_none_without_fundamentals():
    """中线形态必须有基本面才算数，缺了就不给形态。"""
    assert scoring.detect_mid_setup(snap(), None) is None


# ---------------- 评分与归一化 ----------------

def test_score_short_full_data_scores_high():
    out = scoring.score_short(
        snap(),
        flow={"bullish_premium": 900.0, "bearish_premium": 100.0,
              "net_premium": 800.0, "alert_count": 5},
        news={"sentiment_label": "Positive", "significant": "High"})
    assert out["score"] >= 70
    assert out["reasons"]
    assert out["gaps"] == []


def test_score_short_missing_flow_is_renormalized_not_penalised():
    """缺期权数据不该被当成利空，应该把这一项从分母里去掉并写进 gaps。"""
    good = snap()
    with_flow = scoring.score_short(
        good,
        flow={"bullish_premium": 900.0, "bearish_premium": 100.0,
              "net_premium": 800.0, "alert_count": 5},
        news={"sentiment_label": "Positive", "significant": "High"})
    without_flow = scoring.score_short(
        good, flow=None,
        news={"sentiment_label": "Positive", "significant": "High"})
    assert "期权异动" in " ".join(without_flow["gaps"])
    assert without_flow["score"] >= with_flow["score"] - 5


def test_score_short_negative_news_is_a_risk():
    out = scoring.score_short(
        snap(), flow=None,
        news={"sentiment_label": "Negative", "significant": "High"})
    assert any("负面" in r for r in out["risks"])


def test_score_is_always_0_to_100():
    for vol in (0.1, 1.0, 5.0):
        out = scoring.score_short(snap(vol_ratio=vol))
        assert 0 <= out["score"] <= 100


def test_score_short_cn_uses_fund_flow_not_options():
    out = scoring.score_short_cn(
        snap(ticker="600519.SH", market="CN"),
        fund_flow={"net_amount": 5e7, "net_5d": 2e8},
        board={"board_name": "白酒", "net_amount": 1e9, "rank": 3,
               "total_boards": 90},
        sentiment={"limit_up_count": 82, "broken_rate": 0.2})
    assert out["score"] > 0
    assert out["gaps"] == []


def test_score_short_cn_records_missing_board_mapping():
    """A股没有个股→行业映射时必须明写，不能装作算过了。"""
    out = scoring.score_short_cn(
        snap(ticker="600519.SH", market="CN"),
        fund_flow={"net_amount": 5e7, "net_5d": 2e8},
        board=None, sentiment=None)
    assert any("板块" in g for g in out["gaps"])


# ---------------- 止损与目标价 ----------------

def test_plan_trade_stop_is_one_and_a_half_atr_below_entry():
    plan = scoring.plan_trade(snap(), "breakout")
    entry = sum(plan["entry_zone"]) / 2
    assert plan["stop"] == pytest.approx(entry - 1.5 * 4.0, abs=0.01)


def test_plan_trade_targets_are_two_and_three_r():
    plan = scoring.plan_trade(snap(), "breakout")
    entry = sum(plan["entry_zone"]) / 2
    risk = entry - plan["stop"]
    assert plan["targets"][0] == pytest.approx(entry + 2 * risk, abs=0.01)
    assert plan["targets"][1] == pytest.approx(entry + 3 * risk, abs=0.01)
    assert plan["rr_ratio"] == 2.0


def test_plan_trade_without_atr_returns_gaps_not_a_fake_stop():
    """没有 ATR 就别编一个百分比止损出来。"""
    plan = scoring.plan_trade(snap(atr_14=None), "breakout")
    assert plan["stop"] is None
    assert plan["gaps"]


def test_position_stop_takes_the_higher_of_cost_atr_and_ma20():
    out = scoring.position_stop(100.0, snap(atr_14=4.0, ma_20=95.0))
    # 成本 - 2*ATR = 92，20 日均线 = 95，取高的那个
    assert out["suggested_stop"] == 95.0
    assert out["basis"] == "ma_20"
    assert out["breached"] is False


def test_position_stop_flags_breach():
    out = scoring.position_stop(100.0, snap(close=90.0, atr_14=4.0, ma_20=95.0))
    assert out["breached"] is True


def test_position_stop_gives_a_reachable_forward_stop_when_deeply_underwater():
    """浮亏很深时，成本那一档止损位会落在市价上方——那是几周前就破了的
    历史事实。这时必须另给一个在市价下方的止损位，不然「止损」这个词没意义。"""
    out = scoring.position_stop(100.0, snap(close=56.0, atr_14=3.5, ma_20=54.0))
    assert out["breached"] is True
    assert out["suggested_stop"] > 56.0        # 历史止损，已经不可达
    assert out["forward_stop"] == 54.0         # 继续拿的话，止损放这
    assert out["forward_stop"] < 56.0          # 永远在市价下方


def test_position_stop_forward_stop_falls_back_to_price_minus_atr():
    """所有候选都在市价上方时，走 close − 2×ATR 这条兜底。

    上一条测试里 ma_20 在市价下方，直接短路了这条分支——它一次都没被断言碰过，
    改错符号或倍数整套测试都不会红。
    """
    out = scoring.position_stop(100.0, snap(close=90.0, atr_14=4.0, ma_20=95.0))
    assert out["suggested_stop"] == 95.0      # 两个候选都在 90 上方
    assert out["forward_stop"] == 82.0        # 90 − 2×4
    assert out["forward_stop"] < 90.0


# ---------------- 补测（评审修 4）：score_mid、缺均线、异常兜底 ----------------

def test_score_mid_uses_fundamentals():
    fund = {"revenue_growth_yoy": 12.0, "earnings_growth_yoy": 8.0,
            "profit_margin": 15.0, "roe": 18.0,
            "analyst_target_price": 120.0, "week_52_high": 103.0}
    out = scoring.score_mid(snap(), fund=fund,
                            news={"sentiment_label": "Positive",
                                  "significant": "Medium"})
    assert out["score"] >= 70
    assert out["gaps"] == []
    assert any("营收" in r for r in out["reasons"])


def test_score_mid_without_fundamentals_renormalizes():
    """没有基本面时，那 35 分和 52 周高点那 20 分都要退出分母并写进 gaps。"""
    out = scoring.score_mid(snap(), fund=None)
    assert any("基本面" in g for g in out["gaps"])
    assert any("52 周" in g for g in out["gaps"])
    assert 0 <= out["score"] <= 100


def test_missing_moving_averages_is_a_gap_not_a_downtrend():
    """全空的快照没有任何证据说趋势向下，不能编一条「均线全在头顶」出来。"""
    out = scoring.score_short({})
    assert any("均线" in g for g in out["gaps"])
    assert not any("趋势是向下" in r for r in out["risks"])


def test_empty_snapshot_never_raises():
    assert 0 <= scoring.score_short({})["score"] <= 100
    assert 0 <= scoring.score_short_cn({})["score"] <= 100
    assert 0 <= scoring.score_mid({})["score"] <= 100


def test_plan_trade_with_zero_price_does_not_raise():
    """停牌或坏数据可能给出 close=0，不能崩。"""
    plan = scoring.plan_trade(snap(close=0.0), "breakout")
    assert plan["stop_pct"] is None



# ---------------- 复审修 2、修 3：A股主力资金按占比分档、中线基本面按幅度分档 ----------------

def test_score_short_cn_fund_flow_scales_with_share_of_turnover():
    """主力资金不能只看正负。0.06 亿和 2.25 亿都是「流入」，
    但对成交 4 亿和成交 24 亿的票意义完全不同。"""
    strong = scoring.score_short_cn(
        snap(market="CN"),
        fund_flow={"net_amount": 2.25e8, "net_5d": 7.96e8, "amount": 23.67e8})
    weak = scoring.score_short_cn(
        snap(market="CN"),
        fund_flow={"net_amount": 0.06e8, "net_5d": 0.89e8, "amount": 4.27e8})
    assert strong["score"] > weak["score"]


def test_score_short_cn_flags_inflow_without_five_day_follow_through():
    out = scoring.score_short_cn(
        snap(market="CN"),
        fund_flow={"net_amount": 1e8, "net_5d": -3e8, "amount": 10e8})
    assert any("持续性" in r for r in out["risks"])


def test_score_mid_fundamentals_scale_with_magnitude():
    """净利率 0.1% 的公司不该跟净利率 25% 的拿一样的分。"""
    thin = {"revenue_growth_yoy": 0.4, "earnings_growth_yoy": 1.1,
            "profit_margin": 0.1, "week_52_high": 103.0}
    strong = {"revenue_growth_yoy": 28.0, "earnings_growth_yoy": 35.0,
              "profit_margin": 24.0, "roe": 22.0, "week_52_high": 103.0}
    assert (scoring.score_mid(snap(), fund=strong)["score"]
            > scoring.score_mid(snap(), fund=thin)["score"])


def test_score_mid_fundamentals_scale_with_magnitude_holding_fields_equal():
    """跟上一条不同：这里 thin/strong 有的字段完全一样（都带 roe），
    只有数值大小不同——专门排除「有没有某个字段」这种非幅度因素，
    确认真的是幅度分档在起作用，不是字段有无在起作用。"""
    thin = {"revenue_growth_yoy": 0.4, "earnings_growth_yoy": 1.1,
            "profit_margin": 0.1, "roe": 11.0}
    strong = {"revenue_growth_yoy": 28.0, "earnings_growth_yoy": 35.0,
              "profit_margin": 24.0, "roe": 22.0}
    assert (scoring.score_mid(snap(), fund=strong)["score"]
            > scoring.score_mid(snap(), fund=thin)["score"])
