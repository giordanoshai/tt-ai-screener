# -*- coding: utf-8 -*-
"""波段扫描。全市场那条走真实数据，2000 多只票。"""
import json

import pytest

from tools import scan_swing as ss


def run(**kwargs):
    return json.loads(ss.scan_swing(**kwargs))


def test_full_us_scan_returns_capped_candidates():
    out = run(market="US", horizon="short", scope="full", limit=30)
    assert len(out["candidates"]) <= 30
    assert out["scanned"] > 500


def test_every_candidate_has_a_tradeable_plan():
    out = run(market="US", horizon="short", scope="full")
    for c in out["candidates"]:
        assert {"ticker", "setup", "setup_label", "price", "entry_zone",
                "stop", "stop_pct", "targets", "rr_ratio", "score",
                "reasons", "risks", "data_gaps"} <= set(c)


def test_limit_is_capped_at_thirty():
    out = run(market="US", scope="full", limit=999)
    assert len(out["candidates"]) <= 30


def test_sorted_by_score_descending():
    out = run(market="US", scope="full")
    scores = [c["score"] for c in out["candidates"]]
    assert scores == sorted(scores, reverse=True)


def test_short_horizon_drops_names_with_earnings_in_three_days():
    out = run(market="US", horizon="short", scope="full")
    for c in out["candidates"]:
        days = (c.get("earnings") or {}).get("days_until")
        assert days is None or days > 3 or days < 0


def test_mid_horizon_keeps_earnings_but_flags_them():
    out = run(market="US", horizon="mid", scope="full")
    flagged = [c for c in out["candidates"]
               if (c.get("earnings") or {}).get("days_until") is not None
               and 0 <= c["earnings"]["days_until"] <= 3]
    for c in flagged:
        assert any("财报" in r for r in c["risks"])


def test_cn_scan_says_what_is_missing():
    """A股缺个股→行业映射，必须明写，不能装作算过板块。"""
    out = run(market="CN", horizon="short", scope="full")
    gaps = " ".join(out["data_gaps"])
    assert "板块" in gaps


def test_pool_scope_only_covers_positions_and_watchlist():
    out = run(market="US", scope="pool")
    assert out["scope"] == "pool"
    assert out["scanned"] <= 400


def test_bad_market_returns_error_json_not_exception():
    out = run(market="HK")
    assert "error" in out


def test_bad_horizon_returns_error():
    assert "error" in run(market="US", horizon="forever")


def test_both_horizons_labels_each_candidate():
    out = run(market="US", horizon="both", scope="full")
    assert all(c["horizon"] in ("short", "mid") for c in out["candidates"])


def test_regime_is_attached_so_claude_can_frame_the_answer():
    out = run(market="US", scope="full")
    assert out["regime"]["market"] == "US"


# ---------------- 以下两条覆盖 brief 之外新加的两处偏差，brief 给的测试没碰到它们 ----------------


def test_failed_jobs_are_reported_even_when_data_is_fresh():
    """采集任务失败要无条件报。挂在「行情陈旧」下面的话，
    「行情是新的但某个采集任务挂了」会完全不出声——那恰恰最该说。
    跟 tools/review_positions.py 的同名测试是同一个道理。"""
    from core import regime as regime_mod
    from tools import scan_swing as mod

    real = regime_mod.freshness

    def fresh_but_broken(market):
        out = dict(real(market))
        out["stale"] = False
        out["failed_jobs"] = ["us_daily(failed)"]
        out["partial_jobs"] = []
        return out

    try:
        mod.regime.freshness = fresh_but_broken
        out = json.loads(mod.scan_swing(market="US", scope="pool"))
    finally:
        mod.regime.freshness = real
    assert any("采集任务失败" in w and "us_daily" in w for w in out["warnings"])


def test_cn_out_of_sync_signal_dates_reported_as_a_gap():
    """A股四张信号表如果不在同一天，必须明写进 data_gaps，
    不能把三个不同交易日的数据拼成一个假装同步的结论。"""
    from core import cn_signals as cn_mod
    from tools import scan_swing as mod

    real = cn_mod.signal_dates

    def out_of_sync():
        return {"fund_flow": None, "board_flow": None, "limit_pool": None,
                "lhb": None, "in_sync": False,
                "gap": "A股四张信号表不在同一天：测试用假数据探针"}

    try:
        mod.cn_signals.signal_dates = out_of_sync
        out = json.loads(mod.scan_swing(market="CN", scope="pool"))
    finally:
        mod.cn_signals.signal_dates = real
    assert any("不在同一天" in g for g in out["data_gaps"])
