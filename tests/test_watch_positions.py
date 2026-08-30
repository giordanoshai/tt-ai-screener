# -*- coding: utf-8 -*-
"""持仓盯盘。没事就说没事，不制造噪音。"""
import json

import pytest

from tools import watch_positions as wp


def run(**kwargs):
    return json.loads(wp.watch_positions(**kwargs))


def test_quiet_day_returns_a_sentence_not_noise():
    out = run()
    assert isinstance(out["alerts"], list)
    assert out["checked"] >= 0
    if not out["alerts"]:
        assert out["message"]


def test_every_alert_says_what_it_means_and_what_to_do():
    out = run()
    for alert in out["alerts"]:
        assert {"ticker", "kind", "what_happened", "what_it_means",
                "suggested_action", "severity"} <= set(alert)
        assert alert["what_it_means"]
        assert alert["suggested_action"]


def test_alerts_sorted_by_severity():
    out = run()
    severities = [a["severity"] for a in out["alerts"]]
    assert severities == sorted(severities, reverse=True)


def test_price_triggers_are_always_checked_even_without_uw():
    """UW 挂了也要能报「跌破 20 日均线」——那是从库里算的。"""
    out = run()
    assert "price" in out["checked_sources"]


def test_options_flow_threshold_filters_small_trades():
    """单笔 50 万美元以下的期权异动不值得打扰用户。"""
    assert wp.is_big_flow({"total_premium": 100_000}, p90=60) is False
    assert wp.is_big_flow({"total_premium": 900_000}, p90=60) is True


def test_options_flow_top_decile_also_qualifies():
    assert wp.is_big_flow({"total_premium": 100_000, "score": 95}, p90=60) is True


def test_no_positions_says_so():
    out = run()
    if out["checked"] == 0:
        assert "持仓" in out["message"]


def test_returns_json_string_always():
    assert isinstance(wp.watch_positions(), str)


# ---------------- 参数与异常兜底 ----------------

def test_hours_is_clamped_into_valid_range():
    out = run(hours=999999)
    assert out["hours"] <= 24 * 7
    out2 = run(hours=0)
    assert out2["hours"] >= 1


def test_returns_error_json_instead_of_raising():
    """「任何异常都返回 error JSON，不抛」是硬要求，但没有测试逼过这条路。
    把持仓查询打成必抛，看它是不是老实返回 error（同 review_positions.py
    的测法）。"""
    real = wp.pg.query

    def boom(*args, **kwargs):
        raise RuntimeError("数据库炸了")

    try:
        wp.pg.query = boom
        out = run()
    finally:
        wp.pg.query = real
    assert "error" in out
    assert "数据库炸了" in out["error"]


# ---------------- 每一路各自 try/except，一路挂了不连累另一路 ----------------

def test_news_source_failing_does_not_crash_the_tool_or_hide_other_sources(
        monkeypatch):
    """消息这一路挂了：不该抛异常，`checked_sources` 里不该出现 news，
    data_gaps 里要写明是哪一路挂的，其它路（比如期权异动这个 US 持仓
    一定会去查的路）要照常在 checked_sources 里出现。"""
    def boom(*args, **kwargs):
        raise RuntimeError("模拟消息拉取失败")

    monkeypatch.setattr(wp.catalyst, "news_for", boom)
    out = run()
    assert "error" not in out
    assert out["checked"] > 0
    assert "news" not in out["checked_sources"]
    assert any("消息拉取失败" in g for g in out["data_gaps"])
    # 期权异动这一路没有被消息那一路的异常带下水
    assert "options_flow" in out["checked_sources"]


def test_price_snapshot_failing_does_not_crash_the_tool_or_hide_other_sources(
        monkeypatch):
    """价格快照这一路挂了（比如数据库抽风）：不该抛异常，data_gaps 里要
    写明，其它不依赖快照的路（期权异动、UW 实时警报）要照常跑。"""
    def boom(*args, **kwargs):
        raise RuntimeError("模拟行情快照拉取失败")

    monkeypatch.setattr(wp.snapshot, "get", boom)
    out = run()
    assert "error" not in out
    assert any("行情快照拉取失败" in g for g in out["data_gaps"])
    assert "options_flow" in out["checked_sources"]
    # 没有快照，价格触发那几种告警一条都不该出现
    assert not any(a["kind"] in ("stop_breached", "trailing_stop_touched",
                                 "below_ma20", "below_ma50", "new_low")
                  for a in out["alerts"])


def test_earnings_source_failing_is_isolated(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("模拟财报日拉取失败")

    monkeypatch.setattr(wp.catalyst, "earnings_for", boom)
    out = run()
    assert "error" not in out
    assert any("财报日拉取失败" in g for g in out["data_gaps"])
    assert not any(a["kind"] == "earnings_soon" for a in out["alerts"])
    # 价格触发这一路（不依赖 earnings_for）没被带下水
    assert "price" in out["checked_sources"]


def test_uw_live_alert_source_failing_is_isolated(monkeypatch):
    def boom(limit=100):
        raise RuntimeError("模拟 UW 实时警报拉取失败")

    monkeypatch.setattr(wp.uw, "available", lambda: True)
    monkeypatch.setattr(wp.uw, "alerts", boom)
    out = run()
    assert "error" not in out
    assert "uw_alerts" not in out["checked_sources"]
    assert any("UW 实时警报拉取失败" in g for g in out["data_gaps"])
    assert "options_flow" in out["checked_sources"]


def test_uw_unavailable_is_excluded_from_sources_without_error(monkeypatch):
    """没配密钥（或者密钥失效）时 uw.available() 是 False，不该报错，
    也不该出现在 checked_sources 里——跟没配之前的行为一致。"""
    monkeypatch.setattr(wp.uw, "available", lambda: False)
    out = run()
    assert "error" not in out
    assert "uw_alerts" not in out["checked_sources"]
    assert "price" in out["checked_sources"]


def test_regime_freshness_failure_is_isolated_and_still_lets_other_data_through(
        monkeypatch):
    """`regime.freshness()` 自己挂了（比如 ingest_runlog 查询超时）：
    不该报错，要写进 data_gaps，其它数据照常给。"""
    def boom(market):
        raise RuntimeError("模拟新鲜度检测失败")

    monkeypatch.setattr(wp.regime, "freshness", boom)
    out = run()
    assert "error" not in out
    assert any("行情新鲜度检测失败" in g for g in out["data_gaps"])
    assert out["checked"] > 0


def test_cn_signal_source_failure_is_isolated_from_us_sources(monkeypatch):
    """A股那两条路径挂了，不该连累美股那几条已经拿到的结果。"""
    real_query = wp.pg.query

    def fake_query(sql, params=None):
        if "from user_positions" in sql:
            return [{"ticker": "600378.SH", "market": "CN",
                     "avg_cost": 10.0, "shares": 100.0, "currency": "CNY"},
                    {"ticker": "AMZN", "market": "US",
                     "avg_cost": 246.0, "shares": 10.0, "currency": "USD"}]
        return real_query(sql, params)

    def boom(tickers):
        raise RuntimeError("模拟 A股主力资金拉取失败")

    monkeypatch.setattr(wp.pg, "query", fake_query)
    monkeypatch.setattr(wp.cn_signals, "fund_flow", boom)
    out = run()
    assert "error" not in out
    assert any("A股主力资金拉取失败" in g for g in out["data_gaps"])
    assert "options_flow" in out["checked_sources"], (
        "A股那一路挂了，美股期权异动这一路不该被连累")


# ---------------- 采集陈旧 / 失败 / 半途 三种警告，各自能真的被测到 ----------------

def test_freshness_warnings_helper_reports_stale():
    """`_freshness_warnings()` 纯函数：喂一个写死 stale=True 的假 fresh
    字典，不管哪天跑都要能验证「陈旧」这条逻辑还在工作（同
    detect_catalysts.py 的教训——依赖当天真的陈旧的断言，删掉整段逻辑
    照样能绿）。"""
    fresh = {"market": "US", "latest_trading_day": "2020-01-01",
             "days_since": 999, "stale": True,
             "failed_jobs": [], "partial_jobs": []}
    out = wp._freshness_warnings("US", fresh)
    assert any("行情最新只到 2020-01-01" in w and "999" in w for w in out)


def test_freshness_warnings_helper_reports_failed_and_partial_jobs():
    fresh = {"market": "US", "latest_trading_day": "2026-08-28",
             "days_since": 1, "stale": False,
             "failed_jobs": ["us_news(failed)"],
             "partial_jobs": ["us_fundamentals(partial)"]}
    out = wp._freshness_warnings("US", fresh)
    assert any("采集任务失败" in w and "us_news(failed)" in w for w in out)
    assert any("采集任务只跑了一半" in w and "us_fundamentals(partial)" in w
              for w in out)


def test_freshness_warnings_helper_all_clear_produces_nothing():
    fresh = {"market": "US", "latest_trading_day": "2026-08-28",
             "days_since": 1, "stale": False,
             "failed_jobs": [], "partial_jobs": []}
    assert wp._freshness_warnings("US", fresh) == []


def test_stale_warning_reaches_the_tool_output(monkeypatch):
    """光测纯函数还不够——还要确认 `watch_positions()` 真的把
    `regime.freshness()` 的结论接了过去，接线本身没有断。"""
    def fake_freshness(market):
        return {"market": market, "latest_trading_day": "2020-01-01",
                "days_since": 999, "stale": True, "recent_jobs": [],
                "failed_jobs": [], "partial_jobs": []}

    monkeypatch.setattr(wp.regime, "freshness", fake_freshness)
    out = run()
    assert any("行情最新只到" in w for w in out["warnings"])


def test_failed_and_partial_job_warnings_reach_the_tool_output(monkeypatch):
    def fake_freshness(market):
        return {"market": market, "latest_trading_day": "2026-08-28",
                "days_since": 1, "stale": False, "recent_jobs": [],
                "failed_jobs": ["us_news(failed)"],
                "partial_jobs": ["us_fundamentals(partial)"]}

    monkeypatch.setattr(wp.regime, "freshness", fake_freshness)
    out = run()
    assert any("采集任务失败" in w and "us_news" in w for w in out["warnings"])
    assert any("采集任务只跑了一半" in w and "us_fundamentals" in w
              for w in out["warnings"])


def test_failed_job_warning_is_backed_by_real_ingest_runlog():
    """用今天真实的 ingest_runlog 验证，不是只测纯函数。查不到失败任务就
    跳过并说明原因，不放松断言。"""
    from core import regime

    fresh = regime.freshness("US")  # 6 只持仓全是美股
    if not fresh["failed_jobs"]:
        pytest.skip("US 最近没有失败的采集任务，测不出这条警告是不是真的在工作")

    out = run()
    assert any("采集任务失败" in w for w in out["warnings"])


def test_partial_job_warning_is_backed_by_real_ingest_runlog():
    from core import regime

    fresh = regime.freshness("US")
    if not fresh["partial_jobs"]:
        pytest.skip("US 最近没有半途的采集任务，测不出这条警告是不是真的在工作")

    out = run()
    assert any("采集任务只跑了一半" in w for w in out["warnings"])


# ---------------- 修复轮1 / 一：止损告警严重度按跌破幅度分档，不是定值 ----------------

def test_stop_alert_severity_is_not_a_constant():
    """纯函数：跌破越深越严重，且'止损被击穿'(浮亏中)整体比
    '移动止损被触及'(浮盈中)更严重。"""
    deep_breach = wp._stop_alert_severity("stop_breached", 10.0)
    shallow_breach = wp._stop_alert_severity("stop_breached", 1.0)
    deep_touch = wp._stop_alert_severity("trailing_stop_touched", 5.0)
    shallow_touch = wp._stop_alert_severity("trailing_stop_touched", 1.0)

    assert deep_breach > shallow_breach, "跌破越深应该越严重，不能是定值"
    assert shallow_breach > deep_touch, (
        "'止损被击穿'（真亏钱）哪怕只是浅破，也该比'移动止损被触及'（还在赚钱）"
        "的深档更严重——两者不是同一件事")
    assert deep_touch > shallow_touch


def test_stop_breach_severity_scales_with_real_breach_depth_and_pnl_sign():
    """用真实持仓独立算一遍：严重度必须真的随跌破幅度、盈亏方向、以及
    '破了多久'变化，不能是抄回同一个定值。修复轮 3 加了第三个维度
    （评审 C1）：浮亏且最近才破位的是 `stop_breached`（按深浅分 95/75），
    浮亏但已经破位很久的降级成 `stop_breached_longstanding`（固定档，
    远低于 95），浮盈只是碰到移动止损线的是 `trailing_stop_touched`。
    这里独立复算每只票该落在哪个桶、严重度该是多少，跟真实输出核对。"""
    from core import scoring
    from core import snapshot as snap_mod
    from db import pg

    positions = pg.query("select ticker, market, avg_cost, shares "
                         "from user_positions")
    out = run()
    by_key = {(a["ticker"], a["kind"]): a for a in out["alerts"]
             if a["kind"] in ("stop_breached", "stop_breached_longstanding",
                              "trailing_stop_touched")}

    touched, breached_recent, breached_long = [], [], []
    checked = 0
    for pos in positions:
        snap = snap_mod.get([pos["ticker"]], pos["market"]).get(pos["ticker"])
        if not snap or snap.get("close") is None:
            continue
        stop = scoring.position_stop(float(pos["avg_cost"]), snap)
        if not stop.get("breached"):
            continue
        pnl = ((snap["close"] - float(pos["avg_cost"]))
              * float(pos["shares"]))
        depth = abs(stop["distance_pct"])

        if pnl >= 0:
            expected_kind = "trailing_stop_touched"
            expected_severity = wp._stop_alert_severity(expected_kind, depth)
        else:
            # 独立查历史行情，用被测的纯函数（已经有自己的 fixture 单测
            # 单独守着）算连续破位天数，判断落在哪个桶
            hist_rows = pg.query(wp._STOP_HISTORY_SQL,
                                 ([pos["ticker"]], pos["market"],
                                  wp.STOP_HISTORY_LOOKBACK_DAYS))
            consecutive_days, _ = wp._consecutive_breach_days(
                float(pos["avg_cost"]), hist_rows)
            if consecutive_days <= wp.STOP_BREACH_RECENT_DAYS:
                expected_kind = "stop_breached"
                expected_severity = wp._stop_alert_severity(expected_kind, depth)
            else:
                expected_kind = "stop_breached_longstanding"
                expected_severity = wp.SEVERITY["stop_breached_longstanding"]

        alert = by_key.get((pos["ticker"], expected_kind))
        assert alert is not None, (
            f"{pos['ticker']} 应该有一条 {expected_kind} 告警")
        assert alert["severity"] == expected_severity, (
            f"{pos['ticker']} 严重度应该是 {expected_severity}"
            f"（{expected_kind}），实际是 {alert['severity']}")
        bucket = {"trailing_stop_touched": touched,
                 "stop_breached": breached_recent,
                 "stop_breached_longstanding": breached_long}[expected_kind]
        bucket.append((depth, alert["severity"]))
        checked += 1

    assert checked > 0, "今天应该真的有票触发止损相关告警（6 只持仓一直都有）"
    severities = {a["severity"] for a in by_key.values()}
    assert len(severities) > 1, (
        "止损相关告警的严重度全都一样，说明分档没有真正生效")

    if touched and breached_recent:
        shallowest_touch_severity = min(touched, key=lambda t: t[0])[1]
        deepest_breach_severity = max(breached_recent, key=lambda t: t[0])[1]
        assert shallowest_touch_severity < deepest_breach_severity, (
            f"浮盈中只是轻触均线的严重度（{shallowest_touch_severity}）应该"
            f"低于浮亏中远远跌穿止损的严重度（{deepest_breach_severity}）")

    if breached_long and breached_recent:
        # 协调者原话：一只连续破位几十个交易日的票，严重度必须低于一只
        # 今天刚跌破的票
        longstanding_severity = max(s for _, s in breached_long)
        recent_severity = max(s for _, s in breached_recent)
        assert longstanding_severity < recent_severity, (
            f"长期破位的严重度（{longstanding_severity}）应该低于"
            f"最近才破位的严重度（{recent_severity}）")


def test_no_duplicate_below_ma20_when_stop_basis_is_ma20():
    """止损依据就是 20 日均线时，不该再单独发一条「跌破 20 日均线」——
    那是同一件事。今天真实数据里至少有一只票的止损依据是 ma_20。"""
    out = run()
    ma20_stop_tickers = {a["ticker"] for a in out["alerts"]
                         if a["kind"] in ("stop_breached", "stop_breached_longstanding",
                                          "trailing_stop_touched")
                         and "依据：ma_20" in a["what_happened"]}
    if not ma20_stop_tickers:
        pytest.skip("今天没有票的止损依据正好是 ma_20，测不出这条去重")
    below_ma20_tickers = {a["ticker"] for a in out["alerts"]
                          if a["kind"] == "below_ma20"}
    assert not (ma20_stop_tickers & below_ma20_tickers), (
        f"{ma20_stop_tickers & below_ma20_tickers} 同时出现了基于 ma_20 的"
        "止损告警和单独的跌破 20 日均线告警，这是同一件事被算成了两条")


def test_stop_breach_pnl_wording_matches_the_real_sign():
    """`stop_breached`/`stop_breached_longstanding` 只在浮亏时出现，措辞
    必须是「浮亏」且不带负号；`trailing_stop_touched` 只在浮盈时出现，
    措辞必须是「浮盈」。独立算一遍真实盈亏（不复用被测代码的算法）核对。
    修复轮 3 加了 `stop_breached_longstanding`（破位破了很久的降档），
    浮亏时可能是这三个 kind 里的任意一个，不再是非黑即白的两个。"""
    from core import snapshot as snap_mod
    from db import pg

    LOSS_KINDS = ("stop_breached", "stop_breached_longstanding")
    positions = {p["ticker"]: p for p in
                pg.query("select ticker, market, avg_cost, shares "
                        "from user_positions")}
    out = run()
    checked = 0
    for alert in out["alerts"]:
        if alert["kind"] not in LOSS_KINDS + ("trailing_stop_touched",):
            continue
        pos = positions.get(alert["ticker"])
        if not pos:
            continue
        snap = snap_mod.get([pos["ticker"]], pos["market"]).get(pos["ticker"])
        if not snap or snap.get("close") is None:
            continue
        pnl = ((snap["close"] - float(pos["avg_cost"]))
              * float(pos["shares"]))
        text = alert["what_it_means"]
        checked += 1
        assert "-" not in text, (
            f"{alert['ticker']} 盈亏描述里不该出现负号（双重负号读着费解）："
            f"{text}")
        if pnl >= 0:
            assert alert["kind"] == "trailing_stop_touched", (
                f"{alert['ticker']} 实际浮盈 {pnl:.0f}，"
                f"不该被归为 {alert['kind']}")
            assert "浮盈" in text and "浮亏" not in text
        else:
            assert alert["kind"] in LOSS_KINDS, (
                f"{alert['ticker']} 实际浮亏 {pnl:.0f}，"
                f"不该被归为 trailing_stop_touched")
            assert "浮亏" in text and "浮盈" not in text
    assert checked > 0, "样本里应该真的核对到至少一条止损相关告警（今天 6 只持仓都有）"


# ---------------- 修复轮1 / 二：UW 实时警报的方向不能瞎猜 ----------------

# 2026-08-30 真实抓到的样本：GOOGL 的 Stealth Accumulation Scanner，
# 期权是 PUT，ask_volume(1741) 远大于 bid_volume(636)——买方在追价买入
# 看跌期权，是看空；早期写法不管三七二十一一律标成「看多」，方向反了。
_REAL_UW_PUT_SAMPLE = {
    "meta": {"ask_volume": 1741, "bid_volume": 636, "total_premium": "592304"},
    "name": "Stealth Accumulation Scanner",
    "symbol": "GOOGL260831P00347500",
}


def test_uw_live_direction_reads_bearish_from_real_captured_sample():
    assert wp._uw_live_direction(_REAL_UW_PUT_SAMPLE) == "bearish"


def test_uw_live_direction_call_ask_heavy_is_bullish():
    """买看涨 = 看多：在卖价成交、且是看涨期权。"""
    assert wp._uw_live_direction(
        {"symbol": "AAPL260831C00347500",
         "meta": {"ask_volume": 1000, "bid_volume": 100}}) == "bullish"


def test_uw_live_direction_call_bid_heavy_is_bearish():
    """卖看涨 = 看空：在买价成交、且是看涨期权。"""
    assert wp._uw_live_direction(
        {"symbol": "AAPL260831C00347500",
         "meta": {"ask_volume": 100, "bid_volume": 1000}}) == "bearish"


def test_uw_live_direction_put_bid_heavy_is_bullish():
    """卖看跌 = 看多：在买价成交、且是看跌期权。"""
    assert wp._uw_live_direction(
        {"symbol": "AAPL260831P00347500",
         "meta": {"ask_volume": 100, "bid_volume": 1000}}) == "bullish"


def test_uw_live_direction_unresolvable_cases_return_none_not_a_guess():
    """解不出来就说不知道，不猜一个方向。"""
    assert wp._uw_live_direction({"symbol": None, "meta": {}}) is None
    assert wp._uw_live_direction(
        {"symbol": "AAPL", "meta": {"ask_volume": 1, "bid_volume": 1}}) is None
    assert wp._uw_live_direction(
        {"symbol": "AAPL260831P00347500", "meta": {}}) is None
    assert wp._uw_live_direction(
        {"symbol": "AAPL260831P00347500",
         "meta": {"ask_volume": 500, "bid_volume": 500}}) is None


def test_uw_live_alert_uses_resolved_direction_not_hardcoded_bullish(
        monkeypatch):
    """早期 bug：不管三七二十一一律标 big_bullish_flow。真实样本
    （PUT，ask 远大于 bid = 买方追价买看跌 = 看空）应该被判成
    big_bearish_flow，不是 big_bullish_flow。

    这条只测「UW 实时警报的方向解析对不对」，要跟修复轮 2 加的「两路
    信号打架就合并」逻辑隔开——不然如果这只票凑巧同时有一条方向相反的
    期权异动统计告警，两条会被合并成 options_signal_conflict，这条测试
    就测不出方向解析本身对不对了。所以把期权异动统计那一路的原始查询
    钉死成空结果，只留 UW 实时警报这一路。"""
    from db import pg

    held = [r["ticker"] for r in pg.query("select ticker from user_positions")]
    if not held:
        pytest.skip("没有持仓，测不出这条")
    target = held[0]
    fake_item = {
        "symbol": f"{target}260831P00347500",
        "meta": {"underlying_symbol": target,
                 "ask_volume": 1741, "bid_volume": 636},
        "name": "Stealth Accumulation Scanner",
    }

    original_query = wp.pg.query

    def fake_query(sql, params=None):
        if "from flow_alerts_archive" in sql:
            return []  # 期权异动统计这一路钉死成空，隔离掉合并逻辑
        return original_query(sql, params)

    monkeypatch.setattr(wp.pg, "query", fake_query)
    monkeypatch.setattr(wp.uw, "available", lambda: True)
    monkeypatch.setattr(wp.uw, "alerts", lambda limit=100: [fake_item])
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and "实时警报触发" in a["what_happened"]]
    assert len(matched) == 1
    assert matched[0]["kind"] == "big_bearish_flow", (
        f"PUT 且 ask 远大于 bid 是看空，不该被标成 {matched[0]['kind']}")
    assert matched[0]["source"] == "uw_live_alert"


def test_uw_live_alert_direction_unknown_is_reported_not_guessed(monkeypatch):
    """方向解不出来时（比如拿不到 ask_volume/bid_volume），不该硬套一个
    看多/看空的 kind，要如实说不知道并写进 data_gaps。"""
    from db import pg

    held = [r["ticker"] for r in pg.query("select ticker from user_positions")]
    if not held:
        pytest.skip("没有持仓，测不出这条")
    target = held[0]
    fake_item = {
        "symbol": f"{target}260831P00347500",
        "meta": {"underlying_symbol": target},  # 没有 ask_volume/bid_volume
        "name": "Some Custom Rule",
    }
    monkeypatch.setattr(wp.uw, "available", lambda: True)
    monkeypatch.setattr(wp.uw, "alerts", lambda limit=100: [fake_item])
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and a["kind"] == "uw_alert_direction_unknown"]
    assert len(matched) == 1
    assert "方向判断不了" in matched[0]["what_happened"]
    assert any("方向判断不了" in g for g in out["data_gaps"])
    assert not any(a["ticker"] == target
                  and a["kind"] in ("big_bullish_flow", "big_bearish_flow")
                  and "实时警报触发" in a["what_happened"]
                  for a in out["alerts"]), (
        "方向解不出来时不该被误标成看多或看空（注意：这只票可能另外还有"
        "来自期权异动那一路、不相关的 big_bullish_flow/big_bearish_flow，"
        "这里只查 UW 实时警报这一条，用 '实时警报触发' 文案区分）")


# ---------------- 修复轮1 / 三：中性消息不算正面；大盘综述不算个股专属 ----------------

def test_neutral_news_gets_its_own_kind_not_labeled_positive(monkeypatch):
    from db import pg

    held = [r["ticker"] for r in pg.query("select ticker from user_positions")]
    if not held:
        pytest.skip("没有持仓，测不出这条")
    target = held[0]

    def fake_news_for(tickers, days, min_significance, per_ticker):
        return {target: [{"title": "某中性大新闻", "sentiment_label": "Neutral",
                          "significant": "High", "tickers": [target]}]}

    monkeypatch.setattr(wp.catalyst, "news_for", fake_news_for)
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and "某中性大新闻" in a["what_happened"]]
    assert len(matched) == 1
    assert matched[0]["kind"] == "neutral_news"
    assert matched[0]["kind"] not in ("good_news", "bad_news")


def test_market_wide_news_is_not_reported_as_position_specific(monkeypatch):
    """挂票数够多（>= MARKET_WIDE_TICKER_COUNT）的新闻是大盘综述，不是
    这只票的专属消息，直接不发。"""
    from db import pg

    held = [r["ticker"] for r in pg.query("select ticker from user_positions")]
    if not held:
        pytest.skip("没有持仓，测不出这条")
    target = held[0]
    wide_tickers = [target] + [f"FAKE{i}" for i in
                               range(wp.MARKET_WIDE_TICKER_COUNT)]

    def fake_news_for(tickers, days, min_significance, per_ticker):
        return {target: [{"title": "大盘综述新闻", "sentiment_label": "Positive",
                          "significant": "High", "tickers": wide_tickers}]}

    monkeypatch.setattr(wp.catalyst, "news_for", fake_news_for)
    out = run()
    assert not any("大盘综述新闻" in a["what_happened"] for a in out["alerts"])


def test_market_wide_threshold_matches_detect_catalysts_not_a_new_number():
    """这个阈值照抄 detect_catalysts.py 的 MARKET_WIDE_TICKER_COUNT，
    不是凭空另定的数——同一批新闻数据，同一天，判据不该不一样。"""
    from tools import detect_catalysts as dc
    assert wp.MARKET_WIDE_TICKER_COUNT == dc.MARKET_WIDE_TICKER_COUNT


def test_real_neutral_market_wide_news_does_not_become_good_news():
    """2026-08-30 真实抓到的案例：一条 Neutral、挂 8 只票的科技股综述曾被
    报成 AMZN/GOOGL 的「重大正面消息」。用真实数据核对这个具体场景已经
    修好——它现在应该完全不出现在 good_news/bad_news 里（要么被挡在
    大盘综述这道过滤，要么就算漏网也该归到 neutral_news 而不是
    good_news）。"""
    held_rows = json.loads(wp.watch_positions())  # 触发一次，顺带拿 checked
    from db import pg

    held = [r["ticker"] for r in pg.query("select ticker from user_positions")]
    wide_neutral_titles = set()
    for ticker in held:
        for item in wp.catalyst.news_for(
                [ticker], days=1, min_significance="High",
                per_ticker=3).get(ticker, []):
            if (item.get("sentiment_label") == "Neutral"
                    and len(item.get("tickers") or [])
                    >= wp.MARKET_WIDE_TICKER_COUNT):
                wide_neutral_titles.add(item["title"])
    if not wide_neutral_titles:
        pytest.skip("今天没有满足「中性+大盘综述」条件的真实新闻，"
                   "测不出这个具体场景")
    out = run()
    good_bad_titles = {a["what_happened"].split("：", 1)[-1]
                       for a in out["alerts"]
                       if a["kind"] in ("good_news", "bad_news")}
    assert not (wide_neutral_titles & good_bad_titles), (
        f"{wide_neutral_titles & good_bad_titles} 这些中性大盘综述"
        "不该被报成正面/负面消息")


# --------- 修复轮1 / 四 + 评审 C3：「20 交易日新低」的窗口不能含当天 ---------

def _prior_low_key():
    return f"prior_low_{wp.NEW_LOW_WINDOW}"


def test_new_low_window_excludes_today_and_matches_a_manual_count():
    """窗口必须是**今天之前**的 20 根日线，不含今天。

    这条测试上一版跟实现犯了同一个错误：它自己也用 `rn <= 20`（含当天）
    去挑候选票，所以实现把当天算进 min(low) 它照样绿（评审 C3）。现在
    独立按「前 20 根日线」手工复算，并且**专门挑出「今天的最低价就是这
    21 根里最低的那一根」的票**——只有这种票能区分两种窗口：含当天的话
    返回值会等于今天的 low，不含当天才会等于前 20 根的最低。
    """
    from db import pg

    tickers = [r["ticker"] for r in pg.query(
        "select distinct ticker from stock_ohlcv_daily where market='US' "
        "and date = (select max(date) from stock_ohlcv_daily "
        "            where market='US' and date >= current_date - 30)")]
    assert tickers, "拿不到美股票池，这条测试测不出东西"
    rows = {r["ticker"]: r for r in pg.query(wp._NEW_LOW_SQL, (tickers, "US"))}

    key = _prior_low_key()
    checked = discriminating = 0
    for ticker in tickers[:120]:
        manual = pg.query(
            "select low from stock_ohlcv_daily where ticker=%s "
            "and market='US' order by date desc limit 21", (ticker,))
        if len(manual) < 21 or ticker not in rows:
            continue
        today_low = manual[0]["low"]
        expected_prior = min(r["low"] for r in manual[1:])
        got = rows[ticker]
        assert abs(got["today_low"] - today_low) < 1e-6, (
            f"{ticker} 的 today_low 应该是当天的最低价 {today_low}，"
            f"实际是 {got['today_low']}")
        assert abs(got[key] - expected_prior) < 1e-6, (
            f"{ticker} 前 {wp.NEW_LOW_WINDOW} 个交易日的最低价应该是 "
            f"{expected_prior}，实际是 {got[key]}——窗口算错了")
        checked += 1
        if today_low < expected_prior:
            # 这种票才能区分「含当天」和「不含当天」两种窗口
            discriminating += 1
            assert got[key] > today_low, (
                f"{ticker} 今天创了新低，前 20 日最低价不该等于今天的低点"
                "——窗口把当天算进去了")
    assert checked > 50, f"只核对到 {checked} 只票，样本太小"
    assert discriminating > 0, (
        "样本里没有一只票今天创了 20 日新低，这条测试区分不了两种窗口")


def test_new_low_hit_count_is_in_the_right_order_of_magnitude():
    """拿全市场真实数据跑一遍生产用的 SQL + 判定函数，命中数量必须落在
    合理量级（几百只），不是个位数。

    评审 C3 实测：窗口把当天算进去时命中 9 只，按真实语义应该命中 222 只
    ——漏报 96%。数学上 `close ≥ 当日 low ≥ min(含当天的 low)`，所以那种
    写法只有「收盘价正好等于当日最低价」时才成立，命中数必然掉到个位数。
    这条断言就是盯着这个量级差：把窗口改回 `rn <= 21` 含当天，命中数会从
    两百多掉到十位以下，这一行立刻变红。
    """
    from db import pg

    tickers = [r["ticker"] for r in pg.query(
        "select distinct ticker from stock_ohlcv_daily where market='US' "
        "and date = (select max(date) from stock_ohlcv_daily "
        "            where market='US' and date >= current_date - 30)")]
    assert len(tickers) > 1500, f"美股票池只有 {len(tickers)} 只，样本不对"

    key = _prior_low_key()
    hits = [r for r in pg.query(wp._NEW_LOW_SQL, (tickers, "US"))
            if wp.is_new_low(r["today_low"], r[key])]
    # 2026-08-31 实测 218/2082（约 10%）。下限定在 50 只（约 2.4%）——
    # 真实市场再怎么普涨也不至于全市场只有几十只票创 20 日新低，而含当天
    # 那种写法给出的是个位数，中间隔着一个数量级
    assert len(hits) >= 50, (
        f"全市场 {len(tickers)} 只美股里只有 {len(hits)} 只创 20 交易日"
        "新低——这是个位数量级，说明窗口又把当天算进 min(low) 了")

    # 光看命中数还不够：窗口如果含当天，`prior_low` 里就包含了今天的低点，
    # 每一条命中都只能是 `today_low == prior_low`（持平），永远不可能出现
    # 「今天的低点比此前 20 日最低还低一截」。所以额外要求至少有一批票是
    # **严格更低**的——这一条只有窗口真的排除了当天才可能成立
    strictly_lower = [r for r in hits if r["today_low"] < r[key]]
    assert len(strictly_lower) >= 10, (
        f"{len(hits)} 只命中里只有 {len(strictly_lower)} 只是「今天的低点"
        "严格低于此前 20 日最低」——说明 prior_low 里混进了今天自己")


def test_is_new_low_compares_today_low_against_the_prior_window():
    """纯函数：今天的最低价跌破（或持平）此前 20 日最低价才算创新低。
    缺任何一边都不算——判断不了不等于创了新低。"""
    assert wp.is_new_low(9.5, 10.0) is True
    assert wp.is_new_low(10.0, 10.0) is True
    assert wp.is_new_low(10.5, 10.0) is False
    assert wp.is_new_low(None, 10.0) is False
    assert wp.is_new_low(9.5, None) is False


def test_new_low_alert_fires_with_correct_wording_on_a_real_qualifying_ticker(
        monkeypatch):
    """找一只真实数据里今天确实创了 20 交易日新低的票（按**正确**语义挑：
    当日最低价跌破此前 20 个交易日的最低价），验证告警文字把两个数都印
    出来，读的人能自己核对。"""
    from db import pg as real_pg_mod

    candidates = real_pg_mod.query("""
        with recent as (
          select ticker, low, close, date,
                 row_number() over (partition by ticker order by date desc) as rn
          from stock_ohlcv_daily where market = 'US'
        )
        select r1.ticker
        from recent r1
        join (select ticker, min(low) as prior_low from recent
             where rn between 2 and 21 group by ticker) m on m.ticker = r1.ticker
        where r1.rn = 1 and r1.low <= m.prior_low and r1.close > 1
        limit 1
    """)
    if not candidates:
        pytest.skip("今天没有真实票创 20 交易日新低，测不出这条告警")
    target = candidates[0]["ticker"]

    original_query = wp.pg.query

    def fake_query(sql, params=None):
        if "from user_positions" in sql:
            return [{"ticker": target, "market": "US", "avg_cost": 1.0,
                     "shares": 1.0, "currency": "USD"}]
        return original_query(sql, params)

    monkeypatch.setattr(wp.pg, "query", fake_query)
    out = run()
    matched = [a for a in out["alerts"]
              if a["ticker"] == target and a["kind"] == "new_low"]
    assert len(matched) == 1, f"{target} 应该触发一条 new_low 告警"
    text = matched[0]["what_happened"]
    assert "20 个交易日新低" in text
    assert "今日最低" in text and "此前" in text, (
        f"文字要把两个数都说清楚，实际是：{text}")


# ---------------- 期权异动：按票汇总，不按笔数扇出 ----------------

def test_options_flow_alerts_are_aggregated_per_ticker_not_fanned_out(
        monkeypatch):
    """同一只票很多笔大额期权交易时，只出一条告警，不按笔数扇出——
    实测用现有持仓、hours=24 时，仅 AMZN 一只就有三十多笔过线交易，
    如果按笔生成告警，这一路自己就能刷出小一百条（跟
    detect_catalysts.py 那次「一条新闻按票扇出」是同一类问题）。"""
    from db import pg as real_pg

    held = [r["ticker"] for r in
           real_pg.query("select ticker from user_positions "
                         "where market = 'US'")]
    if not held:
        pytest.skip("没有美股持仓，测不出这条聚合逻辑")
    target = held[0]

    # 必须在 monkeypatch 之前把原函数捕获成一个局部变量。`wp.pg` 和
    # `real_pg` 是同一个模块对象——如果 fake_query 内部写
    # `real_pg.query(...)`，等 wp.pg.query 被替换之后，这个属性查找
    # 会读到 fake_query 自己，无限递归（这里真的踩到过一次）。
    original_query = wp.pg.query

    def fake_query(sql, params=None):
        if "from flow_alerts_archive" in sql and "percentile_cont" in sql:
            return [{"ticker": target, "p90": 60.0}]
        if "from flow_alerts_archive" in sql:
            return [
                {"ticker": target, "opt_type": "call", "total_premium": 500_000,
                 "score": 70, "sentiment": "bullish", "day": "2026-08-28",
                 "strike": 100, "expiry": "2026-09-19"},
                {"ticker": target, "opt_type": "put", "total_premium": 700_000,
                 "score": 65, "sentiment": "bearish", "day": "2026-08-28",
                 "strike": 90, "expiry": "2026-09-19"},
                {"ticker": target, "opt_type": "put", "total_premium": 600_000,
                 "score": 55, "sentiment": "bearish", "day": "2026-08-27",
                 "strike": 95, "expiry": "2026-09-19"},
            ]
        return original_query(sql, params)

    monkeypatch.setattr(wp.pg, "query", fake_query)
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and a["kind"] in ("big_bullish_flow", "big_bearish_flow")
              and "笔大额期权交易" in a["what_happened"]]
    assert len(matched) == 1, (
        f"三笔过线交易应该合并成一条告警，不是 {len(matched)} 条")
    assert "3 笔" in matched[0]["what_happened"]
    assert matched[0]["kind"] == "big_bearish_flow", "看跌合计更大，应该定调为偏空"


# ---------------- UW 实时警报：解出标的、同票多规则合并 ----------------

def test_uw_live_alert_maps_underlying_and_merges_multiple_rules(monkeypatch):
    """/api/alerts 的 symbol 是期权合约代码不是股票代码，标的在
    meta.underlying_symbol 里；同一只票命中好几条同方向自定义规则时合并
    成一条，不落在持仓里的票、symbol 为 null 的噪音（potus_schedule）都
    不该生成告警。"""
    from db import pg

    held = [r["ticker"] for r in pg.query("select ticker from user_positions")]
    if not held:
        pytest.skip("没有持仓，测不出这条")
    target = held[0]
    not_held = "ZZZNOTHELDXYZ"

    # Rule A / Rule B 都给同一个可解出方向的 call+ask 占优样本（看多），
    # 这样合并逻辑和方向解析可以分开测——这里只测合并，方向解析在
    # 上面 UW 方向那组测试里单独测过
    common_meta = {"underlying_symbol": target,
                   "ask_volume": 1000, "bid_volume": 50}
    fake_items = [
        {"symbol": f"{target}261002C00250000", "meta": common_meta,
         "name": "Rule A"},
        {"symbol": f"{target}261002C00250000", "meta": common_meta,
         "name": "Rule A"},   # 重复规则名
        {"symbol": f"{target}261002C00251000", "meta": common_meta,
         "name": "Rule B"},
        {"symbol": None, "meta": {}, "noti_type": "potus_schedule"},  # 噪音
        {"symbol": f"{not_held}261002C00250000",
         "meta": {"underlying_symbol": not_held, "ask_volume": 1000,
                  "bid_volume": 50}, "name": "Rule C"},
    ]

    monkeypatch.setattr(wp.uw, "available", lambda: True)
    monkeypatch.setattr(wp.uw, "alerts", lambda limit=100: fake_items)
    out = run()

    assert "uw_alerts" in out["checked_sources"]
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and "实时警报触发" in a["what_happened"]]
    assert len(matched) == 1, "同一只票命中多条规则应该合并成一条，不是每条规则一条"
    assert "Rule A" in matched[0]["what_happened"]
    assert "Rule B" in matched[0]["what_happened"]
    assert matched[0]["what_happened"].count("Rule A") == 1, "重复的规则名不该重复列出"
    assert not any(a["ticker"] == not_held for a in out["alerts"]), (
        "不在持仓里的标的不该生成告警")


# ---------------- A股：主力资金净流出 / 龙虎榜（现有持仓全是美股，覆盖不到，用假持仓测）----------------

def test_cn_outflow_and_lhb_alerts_trigger_on_the_documented_shapes(monkeypatch):
    """现有 6 只持仓全是美股，A股这两条路径真实数据覆盖不到。用一个假的
    CN 持仓 + core.cn_signals 返回的真实数据形状（字段名对着
    core/cn_signals.py 抄）来验证这两条分支真的能触发、告警文案对得上。"""
    real_query = wp.pg.query
    fake_ticker = "600378.SH"

    def fake_query(sql, params=None):
        if "from user_positions" in sql:
            return [{"ticker": fake_ticker, "market": "CN",
                     "avg_cost": 10.0, "shares": 100.0, "currency": "CNY"}]
        return real_query(sql, params)

    def fake_fund_flow(tickers):
        return {t: {"net_5d": -350_000_000.0, "net_amount": -50_000_000.0,
                    "snapshot_date": "2026-08-28"} for t in tickers}

    def fake_lhb(tickers, days=5):
        return {t: [{"list_date": "2026-08-28",
                    "reason": "日内成交异常波动"}] for t in tickers}

    monkeypatch.setattr(wp.pg, "query", fake_query)
    monkeypatch.setattr(wp.cn_signals, "fund_flow", fake_fund_flow)
    monkeypatch.setattr(wp.cn_signals, "lhb", fake_lhb)

    out = run()
    assert "error" not in out
    assert "cn_fund_flow" in out["checked_sources"]
    kinds = {(a["ticker"], a["kind"]) for a in out["alerts"]}
    assert (fake_ticker, "cn_outflow") in kinds
    assert (fake_ticker, "lhb") in kinds
    outflow = next(a for a in out["alerts"]
                  if a["ticker"] == fake_ticker and a["kind"] == "cn_outflow")
    assert "3.50 亿" in outflow["what_happened"]
    lhb_alert = next(a for a in out["alerts"]
                     if a["ticker"] == fake_ticker and a["kind"] == "lhb")
    assert "日内成交异常波动" in lhb_alert["what_happened"]


# ---------------- 返回体大小：如实标注截断，不默默丢数据 ----------------

def test_worst_case_response_stays_within_size_budget():
    """最坏情况（hours 拉满 7 天）也要落在几十 KB 量级内。"""
    out_str = wp.watch_positions(hours=24 * 7)
    size = len(out_str.encode("utf-8"))
    assert size <= 60 * 1024, f"最坏情况返回体 {size} 字节，超出了几十 KB 的目标"


def test_alert_count_and_truncation_are_honest(monkeypatch):
    """把上限压到很小，逼出真的截断，核对 alert_count / returned_count /
    truncated 三个字段互相对得上，而且真的只返回了 MAX_ALERTS 条。"""
    monkeypatch.setattr(wp, "MAX_ALERTS", 2)
    out = run()
    assert out["alert_count"] >= out["returned_count"]
    if out["alert_count"] > 2:
        assert out["truncated"] is True
        assert out["returned_count"] == 2
        assert len(out["alerts"]) == 2
    else:
        assert out["truncated"] is False
        assert out["alert_count"] == out["returned_count"]


def test_no_truncation_when_under_the_cap():
    out = run()
    if not out["truncated"]:
        assert out["alert_count"] == out["returned_count"]
        assert len(out["alerts"]) == out["alert_count"]


# ---------------- 修复轮2 / 一：每条告警要能看出来源；两路期权信号打架不能装作没这回事 ----------------

def test_every_alert_has_a_source_field():
    """每条告警都该带一个说明来源的字段，不能让用户猜这条结论是哪一路
    数据算出来的。"""
    out = run()
    checked = 0
    for alert in out["alerts"]:
        assert alert.get("source"), (
            f"{alert['ticker']}/{alert['kind']} 缺少 source 字段")
        checked += 1
    assert checked > 0, "今天应该真的有告警，不然这条测试测不出东西"


def test_conflicting_option_signals_are_merged_with_disagreement_stated(
        monkeypatch):
    """期权异动统计（数据库聚合，看整体权利金流向）和 UW 实时警报（用户
    自己配置的单笔扫描命中）是两种独立的测量，方向可能合法地不一致。
    这时不能把两条矛盾的结论原样并列摆出来，要合成一条说清楚分歧。

    用假数据构造一个确定性的冲突场景（不依赖 UW /api/alerts 这个滚动
    窗口今天恰好命中什么）：整体权利金流向判成看涨，UW 扫描器命中判成
    看跌（PUT 且 ask 远大于 bid）。"""
    from db import pg

    held = [r["ticker"] for r in
           pg.query("select ticker from user_positions where market = 'US'")]
    if not held:
        pytest.skip("没有美股持仓，测不出这条")
    target = held[0]

    original_query = wp.pg.query

    def fake_query(sql, params=None):
        if "from flow_alerts_archive" in sql and "percentile_cont" in sql:
            return [{"ticker": target, "p90": 60.0}]
        if "from flow_alerts_archive" in sql:
            # 整体权利金流向：看涨占优 -> big_bullish_flow
            return [{"ticker": target, "opt_type": "call",
                     "total_premium": 900_000, "score": 70,
                     "sentiment": "bullish", "day": "2026-08-28",
                     "strike": 100, "expiry": "2026-09-19"}]
        return original_query(sql, params)

    # UW 实时警报：PUT + ask 远大于 bid = 买方追价买看跌 = 看空
    fake_uw_item = {
        "symbol": f"{target}260831P00347500",
        "meta": {"underlying_symbol": target,
                 "ask_volume": 1741, "bid_volume": 636},
        "name": "Stealth Accumulation Scanner",
    }

    monkeypatch.setattr(wp.pg, "query", fake_query)
    monkeypatch.setattr(wp.uw, "available", lambda: True)
    monkeypatch.setattr(wp.uw, "alerts", lambda limit=100: [fake_uw_item])

    out = run()
    conflicts = [a for a in out["alerts"] if a["ticker"] == target
                and a["kind"] == "options_signal_conflict"]
    assert len(conflicts) == 1, "方向相反的两路信号应该合成一条冲突告警"
    alert = conflicts[0]
    assert "不一致" in alert["what_happened"]
    assert "整体权利金流向统计说" in alert["what_happened"]
    assert "UW 实时警报说" in alert["what_happened"]
    assert "options_flow_archive" in alert["source"]
    assert "uw_live_alert" in alert["source"]
    assert alert["severity"] == wp.SEVERITY["options_signal_conflict"]

    # 原来那两条方向各自的结论不该再单独出现——不能原样并列摆出来
    assert not any(a["ticker"] == target
                  and a["kind"] in ("big_bullish_flow", "big_bearish_flow")
                  for a in out["alerts"]), (
        "方向相反时，两条矛盾的原始结论不该被原样并列摆出来")


def test_agreeing_option_signals_are_not_merged(monkeypatch):
    """方向一致时按现在这样正常发，不用合并——两条都保留，各自带来源。"""
    from db import pg

    held = [r["ticker"] for r in
           pg.query("select ticker from user_positions where market = 'US'")]
    if not held:
        pytest.skip("没有美股持仓，测不出这条")
    target = held[0]

    original_query = wp.pg.query

    def fake_query(sql, params=None):
        if "from flow_alerts_archive" in sql and "percentile_cont" in sql:
            return [{"ticker": target, "p90": 60.0}]
        if "from flow_alerts_archive" in sql:
            return [{"ticker": target, "opt_type": "call",
                     "total_premium": 900_000, "score": 70,
                     "sentiment": "bullish", "day": "2026-08-28",
                     "strike": 100, "expiry": "2026-09-19"}]
        return original_query(sql, params)

    # UW 实时警报也判成看涨（call + ask 占优）——跟上面那条同方向
    fake_uw_item = {
        "symbol": f"{target}260831C00347500",
        "meta": {"underlying_symbol": target,
                 "ask_volume": 1741, "bid_volume": 636},
        "name": "Stealth Accumulation Scanner",
    }

    monkeypatch.setattr(wp.pg, "query", fake_query)
    monkeypatch.setattr(wp.uw, "available", lambda: True)
    monkeypatch.setattr(wp.uw, "alerts", lambda limit=100: [fake_uw_item])

    out = run()
    assert not any(a["ticker"] == target and a["kind"] == "options_signal_conflict"
                  for a in out["alerts"]), "方向一致时不该报冲突"
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and a["kind"] == "big_bullish_flow"]
    assert len(matched) == 2, "方向一致时两条应该都正常发出来"
    assert {a["source"] for a in matched} == {"options_flow_archive",
                                               "uw_live_alert"}


def test_real_conflicting_signals_are_merged_when_present_today():
    """协调者复核（2026-08-30/31）时 AMZN、GOOGL 的期权异动统计和 UW
    实时警报方向相反，用真实数据核对过这个具体场景已经修好。但
    `/api/alerts` 是滚动窗口，命中的规则会随时间滚出前 100 条，所以
    「今天真有冲突样本」这个前提不一定每次跑都成立——先查一次今天的
    真实输出确认，不成立就跳过并说明原因，不放松断言（假数据那条测试
    已经把合并逻辑本身钉死了）。"""
    out = run()
    conflicts = {a["ticker"] for a in out["alerts"]
                if a["kind"] == "options_signal_conflict"}
    if not conflicts:
        pytest.skip("这次跑没有票在期权异动统计和 UW 实时警报之间出现"
                   "方向分歧（/api/alerts 是滚动窗口，命中会随时间滚出），"
                   "测不出真实场景下这条合并逻辑，用假数据的那条测试已经"
                   "覆盖了合并逻辑本身")
    for ticker in conflicts:
        assert not any(a["ticker"] == ticker
                       and a["kind"] in ("big_bullish_flow", "big_bearish_flow")
                       for a in out["alerts"])


# ---------------- 修复轮2 / 二：新闻三档要有显式严重度，不能都走默认 30 分 ----------------

def test_news_kinds_have_explicit_severities_not_the_default():
    """一条重大负面消息不该跟一条中性消息同分——三档都要有显式值，且
    相对紧要程度要有意义的顺序：重大负面 > 重大正面 > 情绪中性。"""
    assert wp.SEVERITY["bad_news"] != 30
    assert wp.SEVERITY["good_news"] != 30
    assert wp.SEVERITY["neutral_news"] != 30
    assert wp.SEVERITY["bad_news"] > wp.SEVERITY["good_news"] > wp.SEVERITY["neutral_news"]


def test_bad_news_alert_carries_the_explicit_severity(monkeypatch):
    from db import pg

    held = [r["ticker"] for r in pg.query("select ticker from user_positions")]
    if not held:
        pytest.skip("没有持仓，测不出这条")
    target = held[0]

    def fake_news_for(tickers, days, min_significance, per_ticker):
        return {target: [{"title": "重大负面消息示例",
                          "sentiment_label": "Negative",
                          "significant": "High", "tickers": [target]}]}

    monkeypatch.setattr(wp.catalyst, "news_for", fake_news_for)
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and a["kind"] == "bad_news"]
    assert len(matched) == 1
    assert matched[0]["severity"] == wp.SEVERITY["bad_news"]
    assert matched[0]["severity"] != 30


# ---------------- 修复轮3 / C1（根本问题）：破位要看"什么时候破的" ----------------

def _find_loss_breached_ticker():
    """找一只今天真实浮亏且跌破止损的票，不管它连续破位了多少天。找不到
    返回 None，调用方负责 skip。"""
    from core import scoring
    from core import snapshot as snap_mod
    from db import pg

    positions = pg.query("select ticker, market, avg_cost, shares "
                         "from user_positions")
    for pos in positions:
        snap = snap_mod.get([pos["ticker"]], pos["market"]).get(pos["ticker"])
        if not snap or snap.get("close") is None:
            continue
        stop = scoring.position_stop(float(pos["avg_cost"]), snap)
        if not stop.get("breached"):
            continue
        pnl = ((snap["close"] - float(pos["avg_cost"]))
              * float(pos["shares"]))
        if pnl < 0:
            return pos["ticker"]
    return None


def test_consecutive_breach_days_counts_from_most_recent_backward():
    """纯函数：从最新一天往前数，遇到第一个"没破位"的日子就停。用手造的
    fixture 数据（不查库），每一天用它自己的 ma_20/ATR 重新算一遍止损位。
    avg_cost=100，ATR=2："成本-2×ATR"=96 这一档在这组数据里始终是止损位
    （因为 ma_20 都低于 96）。"""
    avg_cost = 100.0
    rows = [
        {"date": "2026-08-28", "close": 80.0, "ma_20": 90.0, "atr_14": 2.0},
        {"date": "2026-08-27", "close": 82.0, "ma_20": 91.0, "atr_14": 2.0},
        {"date": "2026-08-26", "close": 83.0, "ma_20": 92.0, "atr_14": 2.0},
        {"date": "2026-08-25", "close": 99.0, "ma_20": 93.0, "atr_14": 2.0},  # 99>96，没破位
        {"date": "2026-08-24", "close": 70.0, "ma_20": 94.0, "atr_14": 2.0},
    ]
    count, last_above = wp._consecutive_breach_days(avg_cost, rows)
    assert count == 3
    assert last_above == "2026-08-25"


def test_consecutive_breach_days_all_breached_counts_everything():
    avg_cost = 100.0
    rows = [{"date": f"2026-08-{28-i:02d}", "close": 50.0, "ma_20": 90.0,
            "atr_14": 2.0} for i in range(5)]
    count, last_above = wp._consecutive_breach_days(avg_cost, rows)
    assert count == 5
    assert last_above is None


def test_consecutive_breach_days_empty_rows_is_zero():
    count, last_above = wp._consecutive_breach_days(100.0, [])
    assert count == 0
    assert last_above is None


def test_recent_breach_uses_stop_breached_kind_not_longstanding(monkeypatch):
    """把连续天数强制设成一个小于阈值的数（用 monkeypatch 直接控制
    `_consecutive_breach_days` 的返回值，不依赖今天真实数据恰好是多少
    天），验证走的是 `stop_breached` 这条"最近发生"的分支。"""
    target = _find_loss_breached_ticker()
    if not target:
        pytest.skip("今天没有票同时'浮亏'且'跌破止损'，测不出这条分类")

    monkeypatch.setattr(wp, "_consecutive_breach_days",
                        lambda cost, rows: (1, "2026-08-30"))
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and a["kind"] in ("stop_breached", "stop_breached_longstanding")]
    assert len(matched) == 1
    assert matched[0]["kind"] == "stop_breached"


def test_longstanding_breach_uses_downgraded_kind_and_forward_stop(monkeypatch):
    """把连续天数强制设成一个大于阈值的数，验证走的是
    `stop_breached_longstanding` 这条"持仓状态"的分支：严重度降档、
    不再说"按纪律该减仓或清掉"、原止损位不再被当成今天能用的价位。"""
    target = _find_loss_breached_ticker()
    if not target:
        pytest.skip("今天没有票同时'浮亏'且'跌破止损'，测不出这条分类")

    monkeypatch.setattr(
        wp, "_consecutive_breach_days",
        lambda cost, rows: (wp.STOP_BREACH_RECENT_DAYS + 5, "2026-01-01"))
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and a["kind"] in ("stop_breached", "stop_breached_longstanding")]
    assert len(matched) == 1
    alert = matched[0]
    assert alert["kind"] == "stop_breached_longstanding"
    assert alert["severity"] == wp.SEVERITY["stop_breached_longstanding"]
    assert alert["severity"] < 95
    assert "按纪律该减仓或清掉" not in alert["suggested_action"]
    assert "已经连续 20 个交易日" in alert["what_happened"]


def test_real_longstanding_breach_severity_is_lower_than_a_recent_one():
    """协调者原话：一只连续在止损位下方几十个交易日的票，严重度必须低于
    一只今天刚跌破的票。用真实数据（MP/ORCL 是长期的）核对——前提不成立
    就跳过并说明，不放松断言。"""
    out = run()
    longstanding = [a for a in out["alerts"]
                    if a["kind"] == "stop_breached_longstanding"]
    recent = [a for a in out["alerts"] if a["kind"] == "stop_breached"]
    if not longstanding or not recent:
        pytest.skip("今天没有同时出现'长期破位'和'最近破位'两种真实持仓，"
                   "测不出这条对比（monkeypatch 那两条测试已经把分类逻辑"
                   "本身钉死了）")
    assert (max(a["severity"] for a in longstanding)
           < max(a["severity"] for a in recent))


def test_no_positions_are_stuck_at_top_severity_for_months_old_breaches():
    """协调者复核实测：MP 连续 58 个交易日、ORCL 连续 56 个交易日在止损
    位下方，之前每天都报最高严重度 95。这条断言：真实数据里已经没有
    "严重度 95 但其实是几十个交易日前就破位"的情况——凡是被归为
    `stop_breached_longstanding` 的告警，严重度都不该到 95。

    先查前提是否成立（今天真的有票被归为'长期破位'），不成立就跳过——
    不加这个前提检查的话，'longstanding 列表是空的'也会让下面这条断言
    空对空地通过，测不出'长期降档这整块功能被删掉了'这种更严重的问题
    （踩过这个坑：验证时把 recent 判断硬改成永远 True，longstanding 桶
    整个消失，这条断言在没有前提检查时照样绿）。"""
    out = run()
    longstanding = [a for a in out["alerts"]
                    if a["kind"] == "stop_breached_longstanding"]
    if not longstanding:
        pytest.skip("今天没有票的破位状态被归为'长期破位'，测不出这条")
    longstanding_at_top = [a for a in longstanding if a["severity"] >= 95]
    assert longstanding_at_top == [], (
        f"{longstanding_at_top} 这些'长期破位'的告警不该拿到最高严重度 95")


def test_forward_stop_is_below_current_price_when_used():
    """`stop_breached_longstanding` 的建议动作里如果提到了参考止损位
    （forward_stop），那个数字必须是今天真能用的——低于现价，不能是历史
    上的旧价位。"""
    import re

    from core import snapshot as snap_mod
    from db import pg

    positions = {p["ticker"]: p for p in
                pg.query("select ticker, market from user_positions")}
    out = run()
    longstanding = [a for a in out["alerts"]
                    if a["kind"] == "stop_breached_longstanding"]
    if not longstanding:
        pytest.skip("今天没有票是'长期破位'状态，测不出这条")
    checked = 0
    for alert in longstanding:
        pos = positions.get(alert["ticker"])
        if not pos:
            continue
        snap = snap_mod.get([pos["ticker"]], pos["market"]).get(pos["ticker"])
        if not snap or snap.get("close") is None:
            continue
        matches = re.findall(r"参考止损可以放在 ([\d.]+)", alert["suggested_action"])
        if not matches:
            continue
        forward_value = float(matches[0])
        assert forward_value < snap["close"], (
            f"{alert['ticker']} 的参考止损 {forward_value} 应该低于"
            f"现价 {snap['close']}，不能是历史上的旧价位")
        checked += 1
    assert checked > 0, "样本里应该真的核对到至少一条带 forward_stop 的建议"


# ---------------- 修复轮3 / I2：期权异动一句话不能混两个口径 ----------------

def test_options_flow_direction_wording_uses_a_single_consistent_criterion(
        monkeypatch):
    """看涨/看跌的计数和"最大一笔"的方向判定必须用同一个口径（sentiment），
    不能一半用 sentiment 一半用 opt_type——之前混着说，评审实测近 2 天
    200 行里 88 行两者不一致，输出出现过"看涨 26 笔…最大一笔（PUT，
    行权价 270）"这种自相矛盾的句子。这里故意构造一行 opt_type=CALL 但
    sentiment=bearish 的数据，验证方向词只跟着 sentiment 走。"""
    from db import pg

    held = [r["ticker"] for r in
           pg.query("select ticker from user_positions where market='US'")]
    if not held:
        pytest.skip("没有美股持仓")
    target = held[0]
    original_query = wp.pg.query

    def fake_query(sql, params=None):
        if "from flow_alerts_archive" in sql and "percentile_cont" in sql:
            return [{"ticker": target, "p90": 60.0}]
        if "from flow_alerts_archive" in sql:
            return [{"ticker": target, "opt_type": "CALL",
                     "total_premium": 900_000, "score": 70,
                     "sentiment": "bearish", "day": "2026-08-28",
                     "strike": 100, "expiry": "2026-09-19"}]
        return original_query(sql, params)

    monkeypatch.setattr(wp.pg, "query", fake_query)
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and a["kind"] == "big_bearish_flow"
              and a["source"] == "options_flow_archive"]
    assert len(matched) == 1
    text = matched[0]["what_happened"]
    assert "情绪判定为看跌" in text
    assert "看跌 1 笔" in text
    assert "看涨 0 笔" in text
    assert "CALL 合约" in text  # opt_type 单独标注，不混进方向词


# ---------------- 修复轮3 / I3：期权异动查询按票取，不是全局截断 ----------------

def test_flow_recent_sql_returns_every_qualifying_row_per_ticker():
    """真实验证：`_FLOW_RECENT_SQL` 现在是按票取（unnest + lateral），
    不会因为别的票交易更活跃、全局按溢价排序时被挤出前 N 名而丢行。
    独立按票查一遍 `flow_alerts_archive` 的真实行数，跟 SQL 结果核对
    每票的总数对得上（安全网上限 1000，今天真实活跃度远低于这个数）。"""
    from db import pg

    held = [r["ticker"] for r in
           pg.query("select ticker from user_positions where market='US'")]
    if not held:
        pytest.skip("没有美股持仓")

    days = 8  # 覆盖评审实测过"719 行"的那个窗口量级
    rows = pg.query(wp._FLOW_RECENT_SQL, (held, days))
    got_by_ticker: dict[str, int] = {}
    for r in rows:
        got_by_ticker[r["ticker"]] = got_by_ticker.get(r["ticker"], 0) + 1

    checked = 0
    for ticker in held:
        expected = pg.query_one(
            "select count(*) as n from flow_alerts_archive "
            "where ticker = %s and day >= current_date - %s",
            (ticker, days))["n"]
        expected_capped = min(expected, 1000)
        assert got_by_ticker.get(ticker, 0) == expected_capped, (
            f"{ticker} 期望拿到 {expected_capped} 行，实际拿到 "
            f"{got_by_ticker.get(ticker, 0)} 行——说明按票取这件事没有"
            "真正生效")
        checked += 1
    assert checked > 0, "样本里应该真的核对到至少一只票"


# ---------------- 修复轮3 / I4：小额异动不该被叫成"大资金押注" ----------------

def test_small_options_flow_gets_downgraded_wording_and_severity(monkeypatch):
    """靠 score 分位数这条软门槛过线、但总金额没到 50 万美元硬门槛的期权
    异动，不该被叫成"大资金押注"，严重度也该明显更低——评审真实抓到的
    案例：RKLB 一笔 18.98 万美元的交易被叫成"大资金集中押注"，严重度 70，
    比跌破 20 日均线（60）还高。判据（is_big_flow 的门槛）没有改，改的是
    汇总后的措辞和严重度。

    这里用 30 万美元：落在"过了绝对下限 25 万、但没到 50 万硬门槛"这一段
    ——正是"值得知道但不用动手"的量级（评审 I5 加了绝对下限之后，19 万
    那种整条都不再发了，见 test_tiny_options_flow_is_not_reported_at_all）。
    """
    from db import pg

    held = [r["ticker"] for r in
           pg.query("select ticker from user_positions where market='US'")]
    if not held:
        pytest.skip("没有美股持仓")
    target = held[0]
    original_query = wp.pg.query

    def fake_query(sql, params=None):
        if "from flow_alerts_archive" in sql and "percentile_cont" in sql:
            return [{"ticker": target, "p90": 40.0}]
        if "from flow_alerts_archive" in sql:
            return [{"ticker": target, "opt_type": "put",
                     "total_premium": 300_000, "score": 50,
                     "sentiment": "bearish", "day": "2026-08-28",
                     "strike": 70, "expiry": "2026-09-25"}]
        return original_query(sql, params)

    monkeypatch.setattr(wp.pg, "query", fake_query)
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and a["kind"] == "big_bearish_flow"
              and a["source"] == "options_flow_archive"]
    assert len(matched) == 1
    alert = matched[0]
    # 小额那档的措辞会明说'谈不上大资金押注'——这句话本身含'大资金'三个
    # 字（用来否定它），不能用裸子串判；要判的是没有'有大资金…集中押注'
    # 这句正面断言（那是'大额'那档专属的措辞）
    assert "有大资金在这个方向上集中押注" not in alert["what_happened"]
    assert "有大资金在这个方向上集中押注" not in alert["what_it_means"]
    assert "谈不上" in alert["what_it_means"], (
        "小额那档应该明说'谈不上大资金押注'，不能含糊带过")
    assert alert["severity"] == wp.OPTIONS_FLOW_SMALL_BEARISH_SEVERITY
    assert alert["severity"] < wp.SEVERITY["below_ma20"], (
        "一笔 30 万美元的交易不该比'跌破 20 日均线'还严重")


def test_large_options_flow_keeps_big_money_wording_and_severity(monkeypatch):
    """反过来：总金额真的过了 50 万美元硬门槛时，"大资金押注"这个措辞和
    原有严重度要保留，不能被上面那条降档逻辑误伤。"""
    from db import pg

    held = [r["ticker"] for r in
           pg.query("select ticker from user_positions where market='US'")]
    if not held:
        pytest.skip("没有美股持仓")
    target = held[0]
    original_query = wp.pg.query

    def fake_query(sql, params=None):
        if "from flow_alerts_archive" in sql and "percentile_cont" in sql:
            return [{"ticker": target, "p90": 40.0}]
        if "from flow_alerts_archive" in sql:
            return [{"ticker": target, "opt_type": "put",
                     "total_premium": 900_000, "score": 50,
                     "sentiment": "bearish", "day": "2026-08-28",
                     "strike": 70, "expiry": "2026-09-25"}]
        return original_query(sql, params)

    monkeypatch.setattr(wp.pg, "query", fake_query)
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and a["kind"] == "big_bearish_flow"
              and a["source"] == "options_flow_archive"]
    assert len(matched) == 1
    assert "大资金" in matched[0]["what_it_means"]
    assert matched[0]["severity"] == wp.SEVERITY["big_bearish_flow"]


# ---------------- 修复轮3 / I5：窗口标签要跟实际查询范围对上 ----------------

def test_options_flow_window_label_matches_actual_query_range(monkeypatch):
    """评审实测：`hours=24` 时 SQL 实际覆盖 3 个自然日，但文案曾经写
    "近 2 天"。现在两处必须对上：`hours=24` -> `lookback_days=2` ->
    文案里的天数应该是 `lookback_days+1=3`。"""
    from db import pg

    held = [r["ticker"] for r in
           pg.query("select ticker from user_positions where market='US'")]
    if not held:
        pytest.skip("没有美股持仓")
    target = held[0]
    original_query = wp.pg.query

    def fake_query(sql, params=None):
        if "from flow_alerts_archive" in sql and "percentile_cont" in sql:
            return [{"ticker": target, "p90": 40.0}]
        if "from flow_alerts_archive" in sql:
            return [{"ticker": target, "opt_type": "put",
                     "total_premium": 900_000, "score": 50,
                     "sentiment": "bearish", "day": "2026-08-28",
                     "strike": 70, "expiry": "2026-09-25"}]
        return original_query(sql, params)

    monkeypatch.setattr(wp.pg, "query", fake_query)
    out = run(hours=24)
    matched = [a for a in out["alerts"] if a["ticker"] == target
              and a["kind"] == "big_bearish_flow"
              and a["source"] == "options_flow_archive"]
    assert len(matched) == 1
    assert "近 3 天" in matched[0]["what_happened"], (
        f"hours=24 应该标成'近 3 天'（lookback_days=2，覆盖 3 个自然日），"
        f"实际文案：{matched[0]['what_happened']}")


# ---------------- 修复轮3 / M6：价格数字要四舍五入 ----------------

def test_price_numbers_in_alert_text_are_rounded():
    """现价、均线数值不能带十几位小数糊给用户看（比如
    56.1300010681152）。"""
    import re

    out = run()
    checked = False
    for alert in out["alerts"]:
        for match in re.findall(r"\d+\.\d+", alert["what_happened"]):
            decimals = match.split(".")[1]
            assert len(decimals) <= 2, (
                f"{alert['ticker']}/{alert['kind']} 里的数字 {match} "
                f"小数位数超过 2 位：{alert['what_happened']}")
            checked = True
    assert checked, "样本里应该真的有带小数的数字，不然这条测试测不出东西"


# ---------------- 修复轮3 / M7：uw_alert_direction_unknown 要有显式严重度 ----------------

def test_uw_alert_direction_unknown_has_explicit_severity():
    assert wp.SEVERITY["uw_alert_direction_unknown"] != 30
    assert wp.SEVERITY["uw_alert_direction_unknown"] > wp.SEVERITY["neutral_news"]


# ---------------- 修复轮3 / M8：checked_sources 只在真成功时才报 ----------------

def test_options_flow_source_not_reported_when_query_fails(monkeypatch):
    """查询挂了的话，`options_flow` 不该出现在 `checked_sources` 里——
    早期写法在查询之前就 append 了，查询哪怕失败，`checked_sources` 里
    照样有这一项，会跟 `data_gaps`/`warnings` 里同时报的失败信息自相
    矛盾。"""
    real_query = wp.pg.query

    def boom(sql, params=None):
        if "from flow_alerts_archive" in sql:
            raise RuntimeError("模拟期权异动查询失败")
        return real_query(sql, params)

    monkeypatch.setattr(wp.pg, "query", boom)
    out = run()
    assert "options_flow" not in out["checked_sources"]
    assert any("期权异动拉取失败" in g for g in out["data_gaps"])


def test_news_source_only_reported_when_actual_articles_found(monkeypatch):
    """`catalyst.news_for()` 会给每只查询到的票预填空列表，`if news:`
    这种写法永远为真（只要 dict 非空，不管里面的列表是不是全是空的）。
    要判断的是"有没有真的查到内容"，不是"返回的字典存不存在"。"""
    def fake_news_for(tickers, days, min_significance, per_ticker):
        return {t: [] for t in tickers}  # 字典非空，但每票都是空列表

    monkeypatch.setattr(wp.catalyst, "news_for", fake_news_for)
    out = run()
    assert "news" not in out["checked_sources"]


# ---------------- 修复轮3 / M9：每票新闻上限要在过滤大盘综述之后生效 ----------------

def test_market_wide_article_does_not_starve_a_ticker_of_its_own_news(
        monkeypatch):
    """每票只发一条的上限必须在"过滤掉大盘综述"之后生效——如果这只票
    最新一条新闻恰好是大盘综述、第二条才是真正的个股专属消息，这只票
    当天也该拿到那条个股专属消息，不能因为综述占了唯一名额就整天没有
    新闻告警。"""
    from db import pg

    held = [r["ticker"] for r in pg.query("select ticker from user_positions")]
    if not held:
        pytest.skip("没有持仓")
    target = held[0]
    wide_tickers = [target] + [f"FAKE{i}" for i in
                               range(wp.MARKET_WIDE_TICKER_COUNT)]

    def fake_news_for(tickers, days, min_significance, per_ticker):
        assert per_ticker >= 2, "至少要多拉几条候选，不能只拉 1 条就指望过滤"
        return {target: [
            {"title": "大盘综述排第一条", "sentiment_label": "Positive",
             "significant": "High", "tickers": wide_tickers},
            {"title": "个股专属消息排第二条", "sentiment_label": "Negative",
             "significant": "High", "tickers": [target]},
        ]}

    monkeypatch.setattr(wp.catalyst, "news_for", fake_news_for)
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target]
    assert any("个股专属消息排第二条" in a["what_happened"] for a in matched), (
        "大盘综述占了第一名额，第二条个股专属消息不该被丢掉")
    assert not any("大盘综述排第一条" in a["what_happened"] for a in matched)


def test_option_flow_total_counts_rows_whose_direction_is_unknown(monkeypatch):
    """算「这批期权交易一共多大」时，方向判不出来的那些笔也得算进去。

    库里有大量 sentiment 既不是 bullish 也不是 bearish 的行——近 30 天
    1658 笔，最大一笔 3.68 亿美元，持仓票里 AMZN 就出现过一笔 54 万美元。
    早期写法是 `total_sum = bullish_sum + bearish_sum`，把这些行漏掉了，
    于是一笔已经超过 50 万硬门槛的交易会被判成「量级不算大，谈不上大资金
    押注」。方向跟评审 I4 相反：I4 是把小的说大，这条是把大的说小。

    构造：看涨 20 万 + 看跌 10 万 + 方向未知 40 万。只加前两桶是 30 万，
    不到 50 万门槛；全部加起来 70 万，过门槛。所以漏算那一桶会让这条
    告警从「大额」掉到「量级不算大」。
    """
    from db import pg

    held = pg.query("select ticker from user_positions where market = 'US'")
    if not held:
        pytest.skip("没有美股持仓，这条测不了")
    target = held[0]["ticker"]

    rows = [
        {"ticker": target, "opt_type": "CALL", "total_premium": 200_000.0,
         "score": 90.0, "sentiment": "bullish", "day": "2026-08-30",
         "strike": 100.0, "expiry": "2026-09-18"},
        {"ticker": target, "opt_type": "PUT", "total_premium": 100_000.0,
         "score": 90.0, "sentiment": "bearish", "day": "2026-08-30",
         "strike": 90.0, "expiry": "2026-09-18"},
        {"ticker": target, "opt_type": "CALL", "total_premium": 400_000.0,
         "score": 90.0, "sentiment": "neutral", "day": "2026-08-30",
         "strike": 110.0, "expiry": "2026-09-18"},
    ]
    original_query = wp.pg.query

    def fake_query(sql, params=None):
        if "percentile_cont" in sql and "flow_alerts_archive" in sql:
            return [{"ticker": target, "p90": 0.0}]   # 让三行都算「过线」
        if "from flow_alerts_archive" in sql or "unnest" in sql:
            return rows
        return original_query(sql, params)

    monkeypatch.setattr(wp.pg, "query", fake_query)
    monkeypatch.setattr(wp.uw, "available", lambda: False)  # 隔离掉打架合并

    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
               and a["source"] == "options_flow_archive"]
    assert len(matched) == 1, f"应当只有一条期权异动告警，实际 {len(matched)} 条"
    alert = matched[0]

    assert "大额期权交易" in alert["what_happened"], (
        f"三笔合计 70 万美元已过 50 万门槛，不该被说成量级不大："
        f"{alert['what_happened']}")
    assert alert["severity"] == wp.SEVERITY[alert["kind"]], (
        "过了硬门槛就该拿正常严重度，不是小额那一档")
    assert "方向判不出来的 1 笔" in alert["what_happened"], (
        f"有方向未知的笔数就要说出来，否则「共 3 笔：看涨 1 笔、看跌 1 笔」"
        f"会让人找不到剩下那笔：{alert['what_happened']}")


# ---------------- 评审 I5：期权异动汇总额有绝对下限，低于它整条不发 ----------------

def _fake_flow_query(target, premium):
    original_query = wp.pg.query

    def fake_query(sql, params=None):
        if "from flow_alerts_archive" in sql and "percentile_cont" in sql:
            return [{"ticker": target, "p90": 40.0}]
        if "from flow_alerts_archive" in sql:
            return [{"ticker": target, "opt_type": "put",
                     "total_premium": premium, "score": 50,
                     "sentiment": "bearish", "day": "2026-08-28",
                     "strike": 70, "expiry": "2026-09-25"}]
        return original_query(sql, params)
    return fake_query


def _flow_alerts_for(out, target):
    return [a for a in out["alerts"] if a["ticker"] == target
            and a["source"] == "options_flow_archive"]


def test_tiny_options_flow_is_not_reported_at_all(monkeypatch):
    """上一轮只改了措辞和严重度，没改「发不发」。分位数门槛按定义每只票
    每天都有约 10% 的行满足，等于保证天天有货，直接撞上这个工具「没事就
    说没事」的第一条设计原则——2026-08-31 实测 17 条告警里 3 条是这么来
    的：NVO 汇总 1.4 万美元、MP 2.7 万、RKLB 20.1 万（评审 I5）。

    汇总总额低于 `OPTIONS_FLOW_MIN_TOTAL_PREMIUM` 时整条不发。"""
    from db import pg

    held = [r["ticker"] for r in
           pg.query("select ticker from user_positions where market='US'")]
    if not held:
        pytest.skip("没有美股持仓")
    target = held[0]
    tiny = wp.OPTIONS_FLOW_MIN_TOTAL_PREMIUM - 1

    monkeypatch.setattr(wp.pg, "query", _fake_flow_query(target, tiny))
    out = run()
    assert _flow_alerts_for(out, target) == [], (
        f"{target} 汇总只有 {tiny:.0f} 美元，低于下限 "
        f"{wp.OPTIONS_FLOW_MIN_TOTAL_PREMIUM:.0f}，这条告警根本不该发")


def test_flow_just_over_the_floor_is_still_reported(monkeypatch):
    """反过来：刚过下限的照常发——下限是为了砍噪音，不是把整路信号关掉。
    这一条和上一条成对，缺了任何一条都可能被「干脆一条都不发」蒙混过去。"""
    from db import pg

    held = [r["ticker"] for r in
           pg.query("select ticker from user_positions where market='US'")]
    if not held:
        pytest.skip("没有美股持仓")
    target = held[0]

    monkeypatch.setattr(
        wp.pg, "query",
        _fake_flow_query(target, wp.OPTIONS_FLOW_MIN_TOTAL_PREMIUM + 1))
    out = run()
    matched = _flow_alerts_for(out, target)
    assert len(matched) == 1, (
        f"刚过下限（{wp.OPTIONS_FLOW_MIN_TOTAL_PREMIUM:.0f}+1 美元）"
        "的期权异动应该照常发一条")


def test_options_flow_floor_sits_below_the_hard_big_premium_threshold():
    """下限必须低于 `BIG_PREMIUM`。定成 50 万的话，「靠分位数过线」这条路
    整个死掉，`is_big_flow()` 的第二条判据和「量级不算大」那套措辞会变成
    永远走不到的死代码。"""
    assert 0 < wp.OPTIONS_FLOW_MIN_TOTAL_PREMIUM < wp.BIG_PREMIUM


# ---------------- 评审 I3：大盘综述的阈值只定义一次 ----------------

def test_market_wide_threshold_is_the_one_in_core_not_a_local_copy():
    """五个工具共用同一个数。各自抄一份字面量的话，改一处漏三处。"""
    from core import catalyst as catalyst_mod
    from tools import detect_catalysts as dc
    from tools import review_positions as rp
    assert wp.MARKET_WIDE_TICKER_COUNT is catalyst_mod.MARKET_WIDE_TICKER_COUNT
    assert dc.MARKET_WIDE_TICKER_COUNT is catalyst_mod.MARKET_WIDE_TICKER_COUNT
    # review_positions 直接调 catalyst.is_market_wide()，不再自己存一个数
    assert rp.catalyst.MARKET_WIDE_TICKER_COUNT is (
        catalyst_mod.MARKET_WIDE_TICKER_COUNT)


def test_news_title_none_is_not_printed_as_the_word_none(monkeypatch):
    """上游偶尔抓到链接但没抓到标题。不兜底的话会直接给用户印出
    「重大消息（情绪中性）：None」。"""
    from db import pg

    held = [r["ticker"] for r in pg.query("select ticker from user_positions")]
    if not held:
        pytest.skip("没有持仓")
    target = held[0]

    def fake_news(tickers, days=7, min_significance="Medium", per_ticker=3):
        return {t: ([{"title": None, "url": "http://x", "tickers": [t],
                      "sentiment_label": "Neutral", "significant": "High",
                      "published_at": None}] if t == target else [])
                for t in tickers}

    monkeypatch.setattr(wp.catalyst, "news_for", fake_news)
    out = run()
    matched = [a for a in out["alerts"] if a["ticker"] == target
               and a["source"] == "news"]
    assert len(matched) == 1, "标题缺失也该照常发这条告警，只是文案要兜底"
    assert "None" not in matched[0]["what_happened"], (
        f"标题为空时不能把 None 印给用户：{matched[0]['what_happened']}")
