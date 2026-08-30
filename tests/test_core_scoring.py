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
