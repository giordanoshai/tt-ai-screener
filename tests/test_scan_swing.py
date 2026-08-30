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


# ================ 评审 C2：任何一路数据源挂了，其余候选照常给 ================

def _boom(*args, **kwargs):
    raise RuntimeError("探针：这一路挂了")


_US_SOURCES = [
    ("fundamental", "get", "基本面"),
    ("catalyst", "earnings_for", "财报日"),
    ("catalyst", "news_for", "新闻"),
    ("catalyst", "options_flow", "期权异动"),
    ("catalyst", "options_flow_live", "实时期权流"),
    ("regime", "freshness", "行情新鲜度"),
    ("regime", "get", "市场环境"),
]


def test_every_us_data_source_can_fail_without_killing_the_scan(monkeypatch):
    """每一路取数各写一次「这一路挂了」：其余结论照常返回、`data_gaps` 里
    写明挂的是哪一路。

    评审 C2 实测：改之前，news / 期权流 / regime 任意一路抛异常，返回体就
    只剩 `{"error": ...}`，一只候选都不给。
    """
    baseline = run(market="US", horizon="both", scope="pool")
    assert baseline["candidates"], "今天 pool 里一只候选都没有，这条测试测不出东西"

    for module_name, func_name, label in _US_SOURCES:
        module = getattr(ss, module_name)
        with monkeypatch.context() as mp:
            mp.setattr(module, func_name, _boom)
            out = run(market="US", horizon="both", scope="pool")
        assert "error" not in out, (
            f"{module_name}.{func_name} 挂了就把整个工具打死了："
            f"{out.get('error')}")
        assert out["candidates"], (
            f"{module_name}.{func_name} 挂了之后一只候选都不给了")
        assert any(label in g and "失败" in g for g in out["data_gaps"]), (
            f"{module_name}.{func_name} 挂了，data_gaps 里没写明是"
            f"「{label}」这一路：{out['data_gaps']}")


_CN_SOURCES = [
    ("cn_signals", "fund_flow", "A股主力资金"),
    ("cn_signals", "board_of", "A股板块"),
    ("cn_signals", "limit_sentiment", "A股涨停情绪"),
    ("cn_signals", "signal_dates", "A股信号表日期"),
]


def test_every_cn_data_source_can_fail_without_killing_the_scan(monkeypatch):
    """A股那几路同理。"""
    baseline = run(market="CN", horizon="short", scope="full")
    assert baseline["candidates"], "今天 A股一只候选都没有，这条测试测不出东西"

    for module_name, func_name, label in _CN_SOURCES:
        module = getattr(ss, module_name)
        with monkeypatch.context() as mp:
            mp.setattr(module, func_name, _boom)
            out = run(market="CN", horizon="short", scope="full")
        assert "error" not in out, (
            f"{module_name}.{func_name} 挂了就把整个工具打死了："
            f"{out.get('error')}")
        assert out["candidates"], (
            f"{module_name}.{func_name} 挂了之后一只候选都不给了")
        assert any(label in g and "失败" in g for g in out["data_gaps"]), (
            f"{module_name}.{func_name} 挂了，data_gaps 里没写明是"
            f"「{label}」这一路：{out['data_gaps']}")


# ================ 评审 I1：打分用的期权流和贴出来的证据必须是同一个 ================

def test_scoring_flow_and_live_flow_are_kept_apart_and_labelled():
    """评分依据（库里 5 天的归档）和后补的实时期权流不能挤在同一个键上。

    真实事故：NVDA 的 reasons 写「期权资金偏空（净 −10%）」，旁边
    `flow.net_premium` 却是净 −43.7%；S 的 reasons 写「大致平衡」
    （归档 +1.6%），旁边 `flow` 是 −15.1%。读的人会以为算错了。
    """
    import pytest

    out = run(market="US", horizon="short", scope="pool")
    with_live = [c for c in out["candidates"] if c.get("flow_live")]
    if not with_live:
        pytest.skip("今天没有候选补到实时期权流")

    disagreeing = 0
    for cand in with_live:
        assert cand["flow"], "打分用的那份期权流不该被实时那份顶掉"
        assert cand["flow"]["used_for_score"] is True
        assert cand["flow_live"]["used_for_score"] is False
        assert cand["flow"]["window"] != cand["flow_live"]["window"]
        if cand["flow"]["net_premium"] != cand["flow_live"]["net_premium"]:
            disagreeing += 1
    assert disagreeing > 0, (
        "所有候选两份期权流数字都一样，这条测试区分不了「有没有被覆盖」")


def test_reason_percentage_matches_the_flow_used_for_scoring():
    """reasons 里那个「净 N%」必须能用 `flow`（打分用的那份）算回来，
    不能是实时那份的数。"""
    import pytest

    out = run(market="US", horizon="short", scope="pool")
    checked = 0
    for cand in out["candidates"]:
        flow = cand.get("flow")
        if not flow or not flow.get("alert_count"):
            continue
        bullish = float(flow["bullish_premium"])
        bearish = float(flow["bearish_premium"])
        total = bullish + bearish
        if total <= 0:
            continue
        tilt = (bullish - bearish) / total
        texts = [r for r in cand["reasons"] + cand["risks"]
                 if "期权资金" in r and "%" in r]
        if not texts:
            continue
        expected = f"{tilt:.0%}"
        assert any(expected in t for t in texts), (
            f"{cand['ticker']} 的理由写着 {texts}，但打分用的那份期权流"
            f"算出来是净 {expected}——文字和证据对不上")
        checked += 1
    if checked == 0:
        pytest.skip("今天没有候选带着可核对的期权资金理由")


# ================ 评审 I3：大盘综述不该给消息面满分 ================

def test_market_wide_news_is_not_scored_as_a_stock_catalyst(monkeypatch):
    """一条挂了 7 只票的行业/大盘综述不是这只票的催化剂。
    真实事故：NVDA 靠一条挂 7 只票的《马斯克加速燃气轮机生产之路伴随污染
    难题》拿到消息面满分 20/20。"""
    from core import catalyst

    def wide_good_news(tickers, days=7, min_significance="Medium",
                       per_ticker=3):
        many = [f"FAKE{i}" for i in range(catalyst.MARKET_WIDE_TICKER_COUNT)]
        return {t: [{"title": "大盘综述探针", "title_en": None, "url": None,
                     "published_at": None, "source": "probe",
                     "sentiment_label": "Positive", "sentiment_score": 0.9,
                     "significant": "High", "tickers": many}]
                for t in tickers}

    def narrow_good_news(tickers, days=7, min_significance="Medium",
                         per_ticker=3):
        return {t: [{"title": "个股正面探针", "title_en": None, "url": None,
                     "published_at": None, "source": "probe",
                     "sentiment_label": "Positive", "sentiment_score": 0.9,
                     "significant": "High", "tickers": [t]}]
                for t in tickers}

    with monkeypatch.context() as mp:
        mp.setattr(ss.catalyst, "news_for", wide_good_news)
        wide = run(market="US", horizon="short", scope="pool")
    with monkeypatch.context() as mp:
        mp.setattr(ss.catalyst, "news_for", narrow_good_news)
        narrow = run(market="US", horizon="short", scope="pool")

    assert not any("有正面消息" in r for c in wide["candidates"]
                   for r in c["reasons"]), (
        "大盘综述被当成了这只票的正面催化剂")
    assert any("有正面消息" in r for c in narrow["candidates"]
               for r in c["reasons"]), (
        "个股专属的正面消息应该照常算分——不然上一条断言可以被"
        "「干脆不看新闻」蒙混过去")

    wide_scores = {c["ticker"]: c["score"] for c in wide["candidates"]}
    narrow_scores = {c["ticker"]: c["score"] for c in narrow["candidates"]}
    both = set(wide_scores) & set(narrow_scores)
    assert both, "两次跑没有共同候选，比不出分差"
    assert any(narrow_scores[t] > wide_scores[t] for t in both), (
        "个股专属正面消息和大盘综述打出来的分一模一样，说明过滤没生效")


# ================ 评审 C4 / I2 / 顺手修 ================

def test_every_candidate_reports_how_much_of_the_scorecard_was_used():
    """缺一半依据打出来的 80 分，跟依据齐全打出来的 80 分不是一回事。"""
    out = run(market="US", horizon="both", scope="full")
    assert out["candidates"]
    for cand in out["candidates"]:
        assert 0 < cand["evidence_pct"] <= 100, (
            f"{cand['ticker']} 的 evidence_pct 是 {cand['evidence_pct']}")


def test_earnings_blackout_constant_matches_its_declared_trading_days():
    """模块 docstring 声明「财报 3 个交易日内直接剔除」，而 `days_until`
    是自然日——3 个交易日跨一个周末是 5 个自然日。注释和值必须对得上
    （评审 I2：早期注释写「3 个交易日 ≈ 5 个自然日」，值却是 3）。"""
    from tools import review_positions as rp
    from tools import watch_positions as wp

    assert ss.EARNINGS_BLACKOUT_DAYS == 5
    # 波段新开仓和持仓复核是同一个时间尺度；盯盘要提前打招呼，窗口更长
    assert ss.EARNINGS_BLACKOUT_DAYS == rp.EARNINGS_SOON_DAYS
    assert wp.EARNINGS_SOON_DAYS > ss.EARNINGS_BLACKOUT_DAYS


def test_as_of_is_the_latest_bar_not_whichever_ticker_sorted_first(monkeypatch):
    """`snapshot.get()` 刻意保留停牌退市的票，它们最后一根 K 线不是最新
    交易日。取 `snaps[0]["date"]` 的话，任何一只这种票排在首位就会给整批
    结果贴错日期。

    今天真实数据里恰好第一只票就是最新交易日，所以直接跑不出区别——必须
    人为造一只「排在最前面、日期很旧」的票出来，不然这条测试把实现改回
    `snaps[0]["date"]` 也照样绿。
    """
    import datetime

    from core import snapshot

    latest = snapshot.latest_trading_day("US")
    real_get = snapshot.get
    stale_day = latest - datetime.timedelta(days=90)

    def get_with_a_halted_ticker_first(tickers, market):
        out = real_get(tickers, market)
        for key, snap in out.items():
            if snap:
                # 把第一只有行情的票改成「最后一根 K 线是三个月前」
                snap["date"] = stale_day
                break
        return out

    monkeypatch.setattr(ss.snapshot, "get", get_with_a_halted_ticker_first)
    out = run(market="US", horizon="short", scope="pool")
    if out["as_of"] is None:
        pytest.skip("今天 pool 里没有任何行情")
    assert str(out["as_of"]) != str(stale_day), (
        f"as_of 贴成了那只停牌票的日期 {stale_day}")
    assert str(out["as_of"]) == str(latest)


def test_truncation_is_reported_not_silent():
    """被 limit 砍掉了要说出来，跟另外两个工具一致。"""
    out = run(market="US", horizon="both", scope="full", limit=1)
    assert out["returned_count"] == len(out["candidates"]) == 1
    assert out["truncated"] is True, "全市场两档扫描只返回 1 只，必须标 truncated"
    # 三个基数分开写：命中形态的、排完雷够格的、实际返回的
    assert out["matched"] >= out["qualified"] >= out["returned_count"]


def test_not_truncated_when_everything_fits():
    """反过来：limit 够大时不能乱标 truncated。缺了这一条，
    上面那条可以被「truncated 永远是 True」蒙混过去。"""
    out = run(market="US", horizon="both", scope="full", limit=30)
    if out["qualified"] > 30:
        pytest.skip(f"今天够格的候选有 {out['qualified']} 只，本来就装不下")
    assert out["truncated"] is False
    assert out["returned_count"] == out["qualified"]


def test_candidate_numbers_are_rounded():
    out = run(market="US", horizon="short", scope="full")
    checked = 0
    for cand in out["candidates"]:
        for value in (cand["price"], cand["rsi"], cand["vol_ratio"],
                      cand["atr_pct"]):
            if value is None:
                continue
            assert round(value, 2) == value, f"{cand['ticker']} 的 {value} 没四舍五入"
            checked += 1
    assert checked > 0
