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
    assert not any(a["kind"] in ("stop_breached", "below_ma20", "below_ma50",
                                 "new_low") for a in out["alerts"])


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
    跳过并说明原因，不放松断言（2026-08-30 实测 US 确实有
    us_news(failed)，这条今天应该真的跑到断言那一行，不会跳过）。"""
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


# ---------------- 止损告警：浮盈/浮亏的措辞必须跟真实盈亏方向一致 ----------------

def test_stop_breach_pnl_wording_matches_the_real_sign():
    """2026-08-30 实测抓到的真 bug：止损破位不等于亏钱——均线在成本上方时
    止损本来就抬到均线（护浮盈），这时候破位是「浮盈还在但保护线没了」。
    早期写法一律印「浮亏 {数字}」，把 AMZN 当天浮盈 204 美元印成
    「浮亏 204 USD」，把真亏钱的几只印成「浮亏 -184 USD」这种双重负号。
    这里独立算一遍真实盈亏（不复用被测代码的算法），核对文案措辞。"""
    from core import snapshot as snap_mod
    from db import pg

    positions = {p["ticker"]: p for p in
                pg.query("select ticker, market, avg_cost, shares "
                        "from user_positions")}
    out = run()
    checked = 0
    for alert in out["alerts"]:
        if alert["kind"] != "stop_breached":
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
            assert "浮盈" in text and "浮亏" not in text, (
                f"{alert['ticker']} 实际浮盈 {pnl:.0f}，文案却说：{text}")
        else:
            assert "浮亏" in text and "浮盈" not in text, (
                f"{alert['ticker']} 实际浮亏 {pnl:.0f}，文案却说：{text}")
    assert checked > 0, "样本里应该真的核对到至少一条止损告警（今天 6 只持仓都有）"


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
    meta.underlying_symbol 里；同一只票命中好几条自定义规则时合并成一条，
    不落在持仓里的票、symbol 为 null 的噪音（potus_schedule）都不该生成告警。"""
    from db import pg

    held = [r["ticker"] for r in pg.query("select ticker from user_positions")]
    if not held:
        pytest.skip("没有持仓，测不出这条")
    target = held[0]
    not_held = "ZZZNOTHELDXYZ"

    fake_items = [
        {"symbol": f"{target}261002C00250000",
         "meta": {"underlying_symbol": target}, "name": "Rule A"},
        {"symbol": f"{target}261002C00250000",
         "meta": {"underlying_symbol": target}, "name": "Rule A"},   # 重复规则名
        {"symbol": f"{target}261002P00250000",
         "meta": {"underlying_symbol": target}, "name": "Rule B"},
        {"symbol": None, "meta": {}, "noti_type": "potus_schedule"},  # 噪音
        {"symbol": f"{not_held}261002C00250000",
         "meta": {"underlying_symbol": not_held}, "name": "Rule C"},
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
