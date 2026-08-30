# -*- coding: utf-8 -*-
"""持仓分析。走真实数据（当前 6 只美股持仓）。"""
import json

from tools import review_positions as rp


def run(**kwargs):
    return json.loads(rp.review_positions(**kwargs))


def test_returns_every_position():
    """「每一只」要真的对得上。只判 len >= 1 的话，SQL 里混进一个
    limit 1、六只票丢五只，测试照样绿。"""
    from db import pg

    total = pg.query_one("select count(*) as n from user_positions")
    out = run()
    assert len(out["positions"]) == total["n"]
    first = out["positions"][0]
    assert {"ticker", "market", "currency", "shares", "avg_cost", "price",
            "pnl_pct", "pnl_amount", "trend", "stop", "earnings",
            "news", "flow", "flags", "urgency", "data_gaps"} <= set(first)


def test_summary_buckets_only_count_their_own_currency():
    """跨币种污染要真的测出来。现在只有美元持仓，所以「每个桶只装自己币种」
    这句话得靠跟明细实算比对来验，不能只判没有 total 键。"""
    out = run()
    assert "total" not in out["summary"]
    for currency, block in out["summary"].items():
        mine = [p for p in out["positions"] if p["currency"] == currency]
        assert block["count"] == len(mine)
        expected_cost = sum(p["avg_cost"] * p["shares"] for p in mine)
        assert block["cost_basis"] == round(expected_cost, 2)


def test_stop_breach_flag_matches_the_arithmetic():
    """breached 要跟现价和止损位的大小关系一致。只判键存在的话，
    把它写死成 True 或 False 都发现不了。"""
    out = run()
    checked = 0
    for pos in out["positions"]:
        stop = pos["stop"]
        if pos["price"] is None or stop["suggested_stop"] is None:
            continue
        assert stop["breached"] == (pos["price"] < stop["suggested_stop"])
        assert stop["basis"] in ("ma_20", "成本 - 2×ATR")
        if stop["forward_stop"] is not None:
            assert stop["forward_stop"] < pos["price"]
        checked += 1
    assert checked > 0, "一只票都没验到，这条测试等于没跑"


def test_urgency_order_is_not_just_alphabetical():
    """排序要真的按紧急度。所有票紧急度相同时，这条判据本身就失效了，
    所以先确认确实存在不同的紧急度，再验顺序。"""
    out = run()
    urgencies = [p["urgency"] for p in out["positions"]]
    assert urgencies == sorted(urgencies, reverse=True)
    assert len(set(urgencies)) > 1, (
        "所有持仓紧急度相同，排序无从验证——也说明紧急度分档没起作用")


def test_ticker_filter_narrows_to_one():
    everything = run()
    one = everything["positions"][0]["ticker"]
    out = run(ticker=one)
    assert [p["ticker"] for p in out["positions"]] == [one]


def test_unknown_ticker_returns_empty_not_error():
    out = run(ticker="NOSUCHTICKERXYZ")
    assert out["positions"] == []


def test_news_capped_at_three():
    out = run()
    assert all(len(p["news"]) <= 3 for p in out["positions"])


def test_flags_are_plain_chinese():
    """给用户看的是「跌破止损」，不是 stop_breached。"""
    out = run()
    flags = [f for p in out["positions"] for f in p["flags"]]
    assert all(isinstance(f, str) for f in flags)
    assert not any(f.isascii() for f in flags if f)


def test_returns_error_json_instead_of_raising():
    """「任何异常都返回 error JSON，不抛」是硬要求，但没有测试逼过这条路。
    这里把数据库查询打成必抛，看它是不是老实返回 error。"""
    from tools import review_positions as mod

    real = mod.pg.query

    def boom(*args, **kwargs):
        raise RuntimeError("数据库炸了")

    try:
        mod.pg.query = boom
        out = json.loads(mod.review_positions())
    finally:
        mod.pg.query = real
    assert "error" in out
    assert "数据库炸了" in out["error"]


def test_failed_jobs_are_reported_even_when_data_is_fresh():
    """采集任务失败要无条件报。挂在「行情陈旧」下面的话，
    「行情是新的但某个任务挂了」会完全不出声——那恰恰最该说。"""
    from core import regime as regime_mod
    from tools import review_positions as mod

    real = regime_mod.freshness

    def fresh_but_broken(market):
        out = dict(real(market))
        out["stale"] = False
        out["failed_jobs"] = ["us_daily(failed)"]
        out["partial_jobs"] = []
        return out

    try:
        mod.regime.freshness = fresh_but_broken
        out = json.loads(mod.review_positions())
    finally:
        mod.regime.freshness = real
    assert any("采集任务失败" in w and "us_daily" in w for w in out["warnings"])


# ================ 评审 C1：两个工具对同一只票不能给出打架的结论 ================

#: `watch_positions` 的告警 kind -> `review_positions` 的 `stop_state`。
#: 两边的档位名称故意不一样（一个是「严重度」语境、一个是「紧急度」语境），
#: 但**分到哪一类必须是同一个答案**——判定函数本来就是同一个
#: （`core/scoring.classify_stop_breach`）。
_WATCH_KIND_TO_STATE = {
    "trailing_stop_touched": "trailing_touched",
    "stop_breached": "breached_recent",
    "stop_breached_longstanding": "breached_longstanding",
}


def _watch_stop_alerts():
    from tools import watch_positions as wp
    out = json.loads(wp.watch_positions())
    assert "error" not in out, f"watch_positions 挂了：{out.get('error')}"
    return {a["ticker"]: a for a in out["alerts"]
            if a["kind"] in _WATCH_KIND_TO_STATE}


def test_review_and_watch_agree_on_every_breached_position():
    """同一天、同一只票，两个工具对「跌破止损意味着什么」必须给同一个答案。

    评审 C1 抓到的原始事故：AMZN 浮盈 8.30%、只跌破 0.17%，
    `watch_positions` 说「移动止损被触及 / 严重度 40 / 这笔仓位现在还是浮盈
    204 USD / 不必马上清仓」，`review_positions` 说「跌破止损 / 紧急度 90
    （最高档）/ 按纪律该减仓或清掉」。两个工具是同一个用户同一天连着调的，
    用户先读到哪个就信哪个。

    把 `review_positions` 的降档逻辑删掉（不分盈亏、不看破了多久，一律
    `URGENCY["stop_breached"]`），这条会立刻变红。
    """
    watch = _watch_stop_alerts()
    review = {p["ticker"]: p for p in run()["positions"]}

    checked = 0
    for ticker, alert in watch.items():
        pos = review.get(ticker)
        assert pos is not None, f"{ticker} 在盯盘里有告警，持仓分析里却没有"
        expected = _WATCH_KIND_TO_STATE[alert["kind"]]
        assert pos["stop_state"] == expected, (
            f"{ticker}：盯盘判成 {alert['kind']}（对应 {expected}），"
            f"持仓分析判成 {pos['stop_state']}——两个工具在打架")
        checked += 1
    assert checked > 0, "今天没有任何一只持仓触发止损类信号，这条测试测不出东西"


def test_a_profitable_position_never_gets_the_top_urgency():
    """还在浮盈、只是碰到了护利润的移动止损线的仓位，不该拿最高紧急度。

    这一条单独拎出来，是因为上一条只比「两边分类一不一致」——如果两个工具
    同时退化成「一律最高档」，那条还是绿的。这条盯的是绝对档位。
    """
    import pytest

    out = run()
    profitable_touched = [p for p in out["positions"]
                          if p["stop_state"] == "trailing_touched"]
    if not profitable_touched:
        pytest.skip("今天没有「浮盈中触发移动止损」的持仓")
    for pos in profitable_touched:
        assert pos["pnl_amount"] >= 0, (
            f"{pos['ticker']} 被判成浮盈触线，但 pnl_amount 是 "
            f"{pos['pnl_amount']}")
        assert pos["urgency"] < rp.URGENCY["stop_breached"], (
            f"{pos['ticker']} 还在浮盈 {pos['pnl_amount']}，紧急度却是 "
            f"{pos['urgency']}（最高档 {rp.URGENCY['stop_breached']}）")
        assert any("浮盈" in f for f in pos["flags"]), (
            f"{pos['ticker']} 的 flags 应该说清楚这笔还在浮盈：{pos['flags']}")


def test_longstanding_breach_is_ranked_below_a_recent_one():
    """破了两个月的旧闻不该跟今天刚破位排同一个紧急度。
    前提不成立就跳过并说明，不放松断言。"""
    import pytest

    out = run()
    long_ = [p for p in out["positions"]
             if p["stop_state"] == "breached_longstanding"]
    recent = [p for p in out["positions"]
              if p["stop_state"] == "breached_recent"]
    if not long_ or not recent:
        pytest.skip("今天没有同时出现「长期破位」和「最近破位」两种持仓")
    assert (rp.URGENCY["stop_breached_longstanding"]
            < rp.URGENCY["stop_breached"])
    for pos in long_:
        assert "不是今天的新信号" in " ".join(pos["flags"]), (
            f"{pos['ticker']} 的 flags 应该说清楚这不是今天刚发生的："
            f"{pos['flags']}")


def test_stop_classification_uses_the_shared_core_function(monkeypatch):
    """判定必须走 `core/scoring.classify_stop_breach()`，不许本地再写一套。
    把那个函数换成「一律判成浮盈触线」，`review_positions` 的输出必须跟着变
    ——不变就说明它在用自己的一份逻辑。"""
    import pytest
    from core import scoring

    before = {p["ticker"]: p["stop_state"] for p in run()["positions"]}
    breached = [t for t, v in before.items() if v]
    if not breached:
        pytest.skip("今天没有任何持仓跌破止损")

    monkeypatch.setattr(scoring, "classify_stop_breach",
                        lambda *a, **k: scoring.STOP_STATE_TRAILING)
    after = {p["ticker"]: p["stop_state"] for p in run()["positions"]}
    assert all(after[t] == scoring.STOP_STATE_TRAILING for t in breached), (
        "换掉 core 的判定函数后输出没变，说明这个工具在用自己的一套分类")


# ================ 评审 C2：任何一路数据源挂了，其余结论照常给 ================

_SOURCES = [
    ("snapshot", "get", "行情快照"),
    ("snapshot", "stop_history", "止损历史"),
    ("catalyst", "earnings_for", "财报日"),
    ("catalyst", "news_for", "新闻"),
    ("catalyst", "options_flow_live", "期权异动"),
    ("regime", "freshness", "行情新鲜度"),
    ("cn_signals", "fund_flow", "A股主力资金"),
    ("cn_signals", "lhb", "龙虎榜"),
]


def _boom(*args, **kwargs):
    raise RuntimeError("探针：这一路挂了")


def test_every_data_source_can_fail_without_killing_the_tool(monkeypatch):
    """每一路取数各写一次「这一路挂了」，验证其余结论照常返回、
    `data_gaps` 里写明挂的是哪一路。

    评审 C2 实测：改之前，news / 期权流 / regime 任意一路抛异常，返回体就
    只剩 `{"error": ...}`，一条持仓结论都没有。
    """
    baseline = run()
    assert baseline["positions"], "没有持仓，这条测试测不出东西"
    n = len(baseline["positions"])
    has_cn = any(p["market"] == "CN" for p in baseline["positions"])
    has_us = any(p["market"] == "US" for p in baseline["positions"])

    for module_name, func_name, label in _SOURCES:
        module = getattr(rp, module_name)
        with monkeypatch.context() as mp:
            mp.setattr(module, func_name, _boom)
            out = run()
        assert "error" not in out, (
            f"{module_name}.{func_name} 挂了就把整个工具打死了："
            f"{out.get('error')}")
        assert len(out["positions"]) == n, (
            f"{module_name}.{func_name} 挂了之后持仓少了："
            f"{len(out['positions'])} != {n}")
        # A股/美股专属的那几路，只在真有对应市场的持仓时才会被调到
        if module_name == "cn_signals" and not has_cn:
            continue
        if func_name == "options_flow_live" and not has_us:
            continue
        assert any(label in g and "失败" in g for g in out["data_gaps"]), (
            f"{module_name}.{func_name} 挂了，data_gaps 里没写明是"
            f"「{label}」这一路：{out['data_gaps']}")


# ============ 评审 I3 / 顺手修：大盘综述不抬紧急度，新闻不复制整段原始记录 ============

def test_market_wide_news_does_not_raise_urgency(monkeypatch):
    """一条挂了很多只票的大盘综述不是这只票的专属消息，不许它把紧急度抬到
    `bad_news` 档。真实案例：ORCL 的「有重大负面消息」来自一条挂了 8 只票的
    《大型科技公司AI支出规模远超预期》，把紧急度从 20 抬到了 65。"""
    from core import catalyst

    def wide_bad_news(tickers, days=7, min_significance="Medium", per_ticker=3):
        many = [f"FAKE{i}" for i in range(catalyst.MARKET_WIDE_TICKER_COUNT)]
        return {t: [{"title": "大盘综述探针", "title_en": None, "url": None,
                     "published_at": None, "source": "probe",
                     "sentiment_label": "Negative", "sentiment_score": -0.9,
                     "significant": "High", "tickers": many}]
                for t in tickers}

    monkeypatch.setattr(rp.catalyst, "news_for", wide_bad_news)
    out = run()
    for pos in out["positions"]:
        assert "有重大负面消息" not in pos["flags"], (
            f"{pos['ticker']} 被一条挂了 "
            f"{catalyst.MARKET_WIDE_TICKER_COUNT} 只票的大盘综述抬了紧急度")
        assert pos["news"] and pos["news"][0]["market_wide"] is True


def test_stock_specific_bad_news_still_raises_urgency(monkeypatch):
    """反过来：真正的个股专属重大负面消息照常抬紧急度。缺了这一条，
    上面那条可以被「干脆不看新闻了」蒙混过去。"""
    def narrow_bad_news(tickers, days=7, min_significance="Medium",
                        per_ticker=3):
        return {t: [{"title": "个股负面探针", "title_en": None, "url": None,
                     "published_at": None, "source": "probe",
                     "sentiment_label": "Negative", "sentiment_score": -0.9,
                     "significant": "High", "tickers": [t]}]
                for t in tickers}

    monkeypatch.setattr(rp.catalyst, "news_for", narrow_bad_news)
    out = run()
    assert any("有重大负面消息" in p["flags"] for p in out["positions"]), (
        "个股专属的重大负面消息应该抬紧急度")


def test_news_entries_do_not_carry_the_whole_raw_record():
    """一条新闻挂了两只持仓时，不该把整条原始记录（英文标题 + 它挂的全部
    票的数组）在两只票下面各复制一份——工具只返回算好的结论。"""
    import pytest

    out = run()
    seen = 0
    for pos in out["positions"]:
        for item in pos["news"]:
            assert "tickers" not in item, (
                f"{pos['ticker']} 的新闻里还带着原始 tickers 数组：{item}")
            assert "title_en" not in item, (
                f"{pos['ticker']} 的新闻里还带着英文标题副本：{item}")
            assert isinstance(item["ticker_count"], int)
            seen += 1
    if seen == 0:
        pytest.skip("今天持仓上一条新闻都没有")


def test_prices_are_rounded_before_going_out():
    """`"price": 64.3899993896484` 这种十几位小数没有信息量，别糊到用户脸上。"""
    out = run()
    checked = 0
    for pos in out["positions"]:
        for value in (pos["price"], pos["trend"]["rsi"],
                      pos["trend"]["vol_ratio"], pos["trend"]["atr_pct"]):
            if value is None:
                continue
            assert round(value, 2) == value, (
                f"{pos['ticker']} 的 {value} 没四舍五入")
            checked += 1
    assert checked > 0, "一个数都没核对到，这条测试测不出东西"
