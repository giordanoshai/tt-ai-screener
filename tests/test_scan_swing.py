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


def test_short_horizon_really_drops_a_blackout_candidate():
    """财报排雷要真的被测到。今天恰好没有候选落在 3 天窗口内，
    所以原来那两条测试把过滤删掉也照样绿——必须造一个出来。"""
    real = ss.catalyst.earnings_for
    seen = {}

    def all_reporting_tomorrow(tickers, market):
        out = {t: {"next_earnings_date": None, "last_earnings_date": None,
                   "days_until": 1, "market_cap": None} for t in tickers}
        seen["n"] = len(tickers)
        return out

    try:
        ss.catalyst.earnings_for = all_reporting_tomorrow
        out = run(market="US", horizon="short", scope="full")
    finally:
        ss.catalyst.earnings_for = real
    assert seen["n"] > 0, "一只候选都没有，这条测试证明不了排雷"
    assert out["candidates"] == [], "所有候选明天都财报，短线档应该一个不留"


def test_mid_horizon_keeps_a_blackout_candidate_but_flags_it():
    """中线档相反：财报保留但要标注——持有两个月本来就会跨过财报。"""
    real = ss.catalyst.earnings_for

    def all_reporting_tomorrow(tickers, market):
        return {t: {"next_earnings_date": None, "last_earnings_date": None,
                    "days_until": 1, "market_cap": None} for t in tickers}

    try:
        ss.catalyst.earnings_for = all_reporting_tomorrow
        out = run(market="US", horizon="mid", scope="full")
    finally:
        ss.catalyst.earnings_for = real
    assert out["candidates"], "中线档不该把财报临近的票剔掉"
    assert all(any("财报" in r for r in c["risks"])
               for c in out["candidates"])


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


def test_both_horizons_produces_both_tiers():
    """两档都要真的出现。只判 horizon 是这两个值之一的话，
    把 mid 全标成 short（中线档整个塌掉）也发现不了。"""
    out = run(market="US", horizon="both", scope="full", limit=30)
    tiers = {c["horizon"] for c in out["candidates"]}
    assert tiers <= {"short", "mid"}
    assert tiers == {"short", "mid"}, f"只出现了 {tiers}，中线档或短线档没跑出来"


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


# ---------------- 修复轮 2：资金流连续化后的并列排序、修 1 的自动化测试 ----------------


def test_ties_are_broken_by_volume_not_ticker_code():
    """同分时要按量能排。按股票代码排的话，先后不带任何交易信息。"""
    out = run(market="CN", horizon="short", scope="full")
    cands = out["candidates"]
    for a, b in zip(cands, cands[1:]):
        if a["score"] == b["score"]:
            assert (a["vol_ratio"] or 0) >= (b["vol_ratio"] or 0), (
                f"{a['ticker']} 和 {b['ticker']} 同分 {a['score']}，"
                f"但量能 {a['vol_ratio']} < {b['vol_ratio']}，排序没按量能")


def test_mid_horizon_does_not_report_the_board_gap():
    """中线档走 score_mid，那个函数连 board 参数都没有——
    板块从不参与中线打分，报这个缺口是把缺口说得比实际严重。"""
    mid = run(market="CN", horizon="mid", scope="full")
    short = run(market="CN", horizon="short", scope="full")
    assert not any("板块" in g for g in mid["data_gaps"])
    assert any("板块" in g for g in short["data_gaps"]), (
        "短线档应该报板块缺口，不报的话这条测试证明不了两者的区别")
