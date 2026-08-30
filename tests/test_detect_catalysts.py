# -*- coding: utf-8 -*-
"""突发消息侦测。已经涨完的利好不是机会，是风险。

修复轮 2（评审做了 6 次变异测试，2 次全绿）之后，输出形状变了：一条新闻
只生成一个事件（`tickers` 列出命中的票），不再按票扇出；`price_reaction`
跟着变成按票组织的字典（`{ticker: {...}}`）。下面的测试照新形状写。
"""
import json

import pytest

from tools import detect_catalysts as dc


def run(**kwargs):
    return json.loads(dc.detect_catalysts(**kwargs))


def test_positions_scope_only_covers_held_names():
    out = run(scope="positions", hours=168)
    assert all(e["held"] for e in out["events"])


def test_every_event_says_whether_price_already_reacted():
    """price_reaction 现在是按票组织的字典，每只票各自要有 reacted。"""
    out = run(scope="market", hours=48)
    for event in out["events"]:
        assert "price_reaction" in event
        assert event["price_reaction"], "一个事件至少要命中一只票"
        for ticker, reaction in event["price_reaction"].items():
            assert "reacted" in reaction, f"{ticker} 没有 reacted 字段"


def test_reacted_is_true_when_move_exceeds_two_atr():
    """涨跌幅超过平时波动的两倍，就是已经反应过了。"""
    assert dc.has_reacted({"pct_chg": 9.0, "atr_pct": 3.0, "vol_ratio": 1.0}) is True
    assert dc.has_reacted({"pct_chg": 1.0, "atr_pct": 3.0, "vol_ratio": 1.0}) is False


def test_reacted_is_true_on_volume_alone():
    assert dc.has_reacted({"pct_chg": 0.5, "atr_pct": 3.0, "vol_ratio": 2.5}) is True


def test_reacted_is_none_without_data_not_false():
    """没有行情就说不知道，不能默认「还没反应」骗人去追。"""
    assert dc.has_reacted({"pct_chg": None, "atr_pct": None,
                           "vol_ratio": None}) is None


def test_sorted_held_then_significance_then_unreacted():
    """排序键是「持仓 -> 自选 -> 是不是大盘综述 -> 重要性 -> 是不是所有
    命中的票都已经反应过」。这里独立按输出里能看到的字段重新算一遍期望的
    key 序列，不直接调用模块内部的 `_sort_key`——不然这条测试只是在
    检查「排序时真的调用了 _sort_key」，测不出 _sort_key 自己算错的情况。
    """
    out = run(scope="market", hours=72)
    sig_rank = {"High": 0, "Medium": 1, "Low": 2}

    def all_reacted(event):
        reactions = event["price_reaction"].values()
        return bool(reactions) and all(r["reacted"] is True for r in reactions)

    keys = [(not e["held"], not e["watched"], e["market_wide"],
             sig_rank.get(e["significance"], 3), all_reacted(e))
            for e in out["events"]]
    assert keys == sorted(keys)


def test_is_market_wide_threshold():
    """挂的票数够多就是大盘综述，够不到就不是。"""
    assert dc.is_market_wide(dc.MARKET_WIDE_TICKER_COUNT - 1) is False
    assert dc.is_market_wide(dc.MARKET_WIDE_TICKER_COUNT) is True
    assert dc.is_market_wide(13) is True


def test_market_wide_flag_matches_ticker_count_in_db():
    """大盘范围的标记要跟数据库里这条新闻实际挂的票数对得上，不是摆设。

    直接绕开 detect_catalysts 自己的判断，独立查库拿到每条新闻真实挂了
    几只票，再用 is_market_wide() 重新算一遍期望值，跟工具吐出来的
    market_wide 比对。用 url 而不是只用标题+时间来定位——同标题同时间的
    新闻不一定只有一行（M3：修复轮 2 评审指出的定位不唯一问题）。
    """
    from db import pg

    out = run(scope="market", hours=72, min_significance="Low")
    news_events = [e for e in out["events"] if e["source_table"] == "news"]
    assert news_events, "样本里应该有 news 表来源的事件，不然这条测试测不出东西"

    checked_wide = checked_narrow = False
    for event in news_events:
        rows = pg.query(
            "select array_length(related_tickers, 1) as n from news "
            "where coalesce(title_cn, title) = %s and published_at = %s "
            "and url = %s",
            (event["event"], event["published_at"], event["url"]))
        if len(rows) != 1 or rows[0]["n"] is None:
            # 定位不到唯一一行就跳过，不拿模糊匹配的结果去断言
            continue
        expected = dc.is_market_wide(rows[0]["n"])
        assert event["market_wide"] == expected, (
            f"{event['event'][:30]!r} 挂了 {rows[0]['n']} 只票，"
            f"market_wide 应该是 {expected}，实际是 {event['market_wide']}")
        if expected:
            checked_wide = True
        else:
            checked_narrow = True
    assert checked_wide, "样本里应该真的核对到一条被判成大盘综述的新闻"
    assert checked_narrow, "样本里应该真的核对到一条被判成个股专属的新闻"


def test_market_wide_counts_tickers_before_filtering_by_scope(monkeypatch):
    """I2：挂了 7 只以上票的新闻，即使过滤到只剩 1~2 只落在持仓范围内，
    也要保持 market_wide=True——票数要在按 scope 过滤之前数。若把数票数
    挪到过滤之后，持仓一共只有 6 只票，`tickers` 过滤完永远凑不够阈值，
    market_wide 会一直是 False（评审员真的这么改过，判定大盘综述的三条
    测试全部照样通过——因为它们全用 scope="market"，压根没有发生过滤）。

    这里把 MAX_EVENTS 临时调大，单独测这条计数顺序的语义，不跟「返回体
    太大被砍」纠缠——持仓范围里事件总数一大，大盘综述天然排在后面，默认
    上限下很可能被砍没了，那是 C1 那条上限该测的事，不是这条。
    """
    monkeypatch.setattr(dc, "MAX_EVENTS", 100000)
    out = run(scope="positions", hours=336, min_significance="Low")
    wide = [e for e in out["events"] if e["market_wide"]]
    assert wide, ("持仓范围内应该真的能找到至少一条挂票数够多、但落在"
                  "持仓里只有一两只的大盘综述")
    for e in wide:
        assert len(e["tickers"]) < dc.MARKET_WIDE_TICKER_COUNT, (
            f"{e['event'][:20]!r} 落在持仓范围内的票是 {e['tickers']}，数量"
            f"不可能达到阈值——market_wide=True 只能是按过滤前的原始票数"
            f"判出来的")


def test_market_wide_never_ranks_ahead_of_individual_in_same_tier():
    """同一个「是不是持仓/是不是自选」身份桶里，大盘综述不许排在个股专属
    消息前面——这正是修复轮 1 要解决的「大盘综述被包装成个股催化剂」。"""
    out = run(scope="market", hours=72, min_significance="Low")
    wide = [e for e in out["events"] if e["market_wide"]]
    narrow = [e for e in out["events"] if not e["market_wide"]]
    assert wide, "样本里应该真的有大盘综述"
    assert narrow, "样本里应该真的有个股专属消息"

    position_of = {id(e): i for i, e in enumerate(out["events"])}
    for w in wide:
        for n in narrow:
            if w["held"] == n["held"] and w["watched"] == n["watched"]:
                assert position_of[id(n)] < position_of[id(w)], (
                    f"{n['event'][:20]!r}（个股专属）排到了"
                    f"{w['event'][:20]!r}（大盘综述）后面")


def test_price_reaction_numbers_are_rounded():
    """涨跌幅/量比/ATR 只留 2 位小数，不能把行情快照里的十几位小数原样透传。"""
    out = run(scope="market", hours=72, min_significance="Low")
    checked = False
    for event in out["events"]:
        for ticker, reaction in event["price_reaction"].items():
            for key in ("pct_chg", "vol_ratio", "atr_pct"):
                value = reaction[key]
                if value is not None:
                    checked = True
                    assert value == round(value, 2), (
                        f"{ticker} 的 {key} 没有四舍五入到 2 位小数：{value}")
    assert checked, "样本里应该真的有非空的价格数字，不然这条测试测不出东西"


def test_price_reaction_states_which_day_the_move_is_from():
    """reacted 只看最新交易日一天的涨跌，字段里要带上具体是哪一天
    （as_of），不能让人以为是消息发布以来的累计涨跌。"""
    out = run(scope="positions", hours=168)
    checked = False
    for event in out["events"]:
        for ticker, reaction in event["price_reaction"].items():
            if reaction["reacted"] is not None:
                checked = True
                assert reaction["as_of"] is not None
    assert checked, "样本里应该真的有算出反应结果的事件"


def test_watchlist_scope_marks_watched():
    out = run(scope="watchlist", hours=168)
    assert all(e["watched"] or e["held"] for e in out["events"])


def test_news_source_appears_when_it_has_data():
    """I1：这条测试名叫「合并两个新闻源」，原来的子集断言
    （`sources <= {"news","uw_news"}`）空集也满足，评审员把 uw_news 那一路
    整个删掉照样全绿。改成断言 news 表真的贡献了事件——先查一次库确认
    这个窗口内 news 表确实有数据，不然遇到某天真的没数据会假红。"""
    from db import pg

    hours = 72
    count = pg.query_one(
        "select count(*) as n from news where published_at >= "
        "now() - make_interval(hours => %s)", (hours,))["n"]
    if not count:
        pytest.skip(f"news 表最近 {hours} 小时内没有数据，测不出合并逻辑")

    out = run(scope="market", hours=hours)
    sources = {e["source_table"] for e in out["events"]}
    assert "news" in sources, "news 表这个窗口内明明有数据，工具却没吐出任何一条"


def test_uw_news_source_appears_when_it_has_data():
    """I1 的另一半：uw_news 那一路也要真的验到，不能只测 news 那一路。"""
    from db import pg

    hours = 72
    count = pg.query_one(
        "select count(*) as n from uw_news where created_at >= "
        "now() - make_interval(hours => %s)", (hours,))["n"]
    if not count:
        pytest.skip(f"uw_news 表最近 {hours} 小时内没有数据，测不出合并逻辑")

    out = run(scope="market", hours=hours)
    sources = {e["source_table"] for e in out["events"]}
    assert "uw_news" in sources, ("uw_news 表这个窗口内明明有数据，"
                                  "工具却没吐出任何一条")


def test_merges_both_news_sources():
    """两路加起来也不能出现第三个来源，兜个底。"""
    out = run(scope="market", hours=72)
    sources = {e["source_table"] for e in out["events"]}
    assert sources <= {"news", "uw_news"}


def test_bad_scope_returns_error():
    assert "error" in run(scope="everything")


def test_no_events_is_a_sentence_not_an_empty_blob():
    out = run(scope="positions", hours=1)
    if not out["events"]:
        assert out["message"]


# ---------------- C1：返回体大小、去重、字段不该泄漏内部排序辅助值 ----------------

def test_one_news_item_becomes_one_event_not_fanned_out_per_ticker():
    """一条新闻只生成一个事件——同一个来源、同一个标题、同一个发布时间
    不会重复出现，即使这条新闻同时挂了好几只在 scope 范围内的票。"""
    out = run(scope="watchlist", hours=336, min_significance="Low")
    assert out["events"], "样本里应该真的有事件，不然这条测试测不出东西"
    keys = [(e["source_table"], e["event"], str(e["published_at"]))
            for e in out["events"]]
    assert len(keys) == len(set(keys)), "同一条新闻被重复算成了多条事件"

    # 顺带确认「同一条新闻挂了好几只范围内的票」这件事真的发生过、
    # 而且被合并进了同一个事件的 tickers 列表里，不是这个场景根本没出现过
    assert any(len(e["tickers"]) > 1 for e in out["events"]), (
        "样本里应该有至少一条事件命中了不止一只票，不然「不按票扇出」这件事"
        "没有被真正测到")


def test_worst_case_response_stays_within_size_budget():
    """最坏情况（watchlist, 336h, Low）的返回体要落在 MAX_EVENTS 定的
    上限量级内（几十 KB），不能是当年 254 KB 那种原始表大小。"""
    out_str = dc.detect_catalysts(scope="watchlist", hours=336,
                                  min_significance="Low")
    size = len(out_str.encode("utf-8"))
    assert size <= 60 * 1024, f"最坏情况返回体 {size} 字节，超出了几十 KB 的目标"

    out = json.loads(out_str)
    assert out["truncated"] is True, "这个场景本来就该触发截断，不然测不出上限"
    assert out["returned_count"] == dc.MAX_EVENTS
    assert len(out["events"]) == dc.MAX_EVENTS


def test_event_count_and_returned_count_are_honest():
    """总共匹配了多少条、实际返回了多少条要如实写出来，不能默默截断。"""
    out = run(scope="watchlist", hours=336, min_significance="Low")
    assert out["event_count"] >= out["returned_count"]
    if out["truncated"]:
        assert out["event_count"] > out["returned_count"]
        assert out["returned_count"] == dc.MAX_EVENTS
    else:
        assert out["event_count"] == out["returned_count"]


def test_significance_rank_is_not_leaked_to_the_caller():
    """`_significance_rank` 是排序用的内部辅助值，不该出现在返回体里
    （修复轮 2 / C1，原来在 tools/detect_catalysts.py:244）。"""
    out = run(scope="market", hours=72, min_significance="Low")
    assert out["events"], "样本里应该真的有事件"
    for event in out["events"]:
        assert "_significance_rank" not in event


# ---------------- I3：一路数据源挂了不能连累另一路 ----------------

def test_news_source_failure_does_not_take_down_uw_news(monkeypatch):
    """news 表这一路挂了，uw_news 那一路本来能拿到的结果不能跟着丢，
    而且 data_gaps 里要写明挂的是哪一路。"""
    from db import pg

    hours = 72
    uw_count = pg.query_one(
        "select count(*) as n from uw_news where created_at >= "
        "now() - make_interval(hours => %s)", (hours,))["n"]
    if not uw_count:
        pytest.skip(f"uw_news 最近 {hours} 小时内没有数据，测不出隔离效果")

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟 news 表查询失败")

    monkeypatch.setattr(dc.catalyst, "news_recent", _boom)

    out = run(scope="market", hours=hours)
    assert "error" not in out
    assert any(e["source_table"] == "uw_news" for e in out["events"]), (
        "news 那一路挂了，uw_news 的结果应该照常返回")
    assert any(e["source_table"] == "news" for e in out["events"]) is False
    assert any("news" in gap for gap in out["data_gaps"]), (
        "data_gaps 里应该写明是 news 这一路挂了")


def test_uw_news_source_failure_does_not_take_down_news(monkeypatch):
    """反过来：uw_news 这一路挂了，news 表那一路照常返回。"""
    from db import pg

    hours = 72
    news_count = pg.query_one(
        "select count(*) as n from news where published_at >= "
        "now() - make_interval(hours => %s)", (hours,))["n"]
    if not news_count:
        pytest.skip(f"news 最近 {hours} 小时内没有数据，测不出隔离效果")

    original_query = dc.pg.query

    def _boom_on_uw_news(sql, params=None):
        if "uw_news" in sql:
            raise RuntimeError("模拟 uw_news 查询失败")
        return original_query(sql, params)

    monkeypatch.setattr(dc.pg, "query", _boom_on_uw_news)

    out = run(scope="market", hours=hours)
    assert "error" not in out
    assert any(e["source_table"] == "news" for e in out["events"]), (
        "uw_news 那一路挂了，news 表的结果应该照常返回")
    assert any("uw_news" in gap for gap in out["data_gaps"]), (
        "data_gaps 里应该写明是 uw_news 这一路挂了")


# ---------------- M1：空字符串代码不能生成垃圾事件 ----------------

def test_clean_tickers_drops_empty_and_blank_strings():
    assert dc._clean_tickers(["AAPL", "", None, "   ", "MSFT"], None) == [
        "AAPL", "MSFT"]
    assert dc._clean_tickers(["AAPL", "MSFT", ""], {"MSFT"}) == ["MSFT"]
    assert dc._clean_tickers([], None) == []
    assert dc._clean_tickers(["", None, "  "], None) == []


# ---------------- I3 附带：行情陈旧也要报（另外三个工具都有） ----------------

def test_market_freshness_warning_is_consistent_with_regime():
    """行情陈不陈旧要跟 core.regime.freshness() 说的一致，不能是摆设——
    工具自己不该重新发明一套「陈旧」的判法，只能如实转述 regime 的结论。"""
    from core import regime

    out = run(scope="positions", hours=72)
    fresh = regime.freshness("US")  # 6 只持仓全是美股
    mentions_stale = any("行情最新只到" in w for w in out["warnings"])
    assert mentions_stale == bool(fresh["stale"])


def test_price_reaction_stale_flag_matches_snapshot():
    """每只票的 `stale` 要跟独立查一次 snapshot.get() 算出来的结果对得上，
    不能是写死的常量。"""
    from core import snapshot
    from db import pg

    positions = pg.query("select ticker, market from user_positions")
    market_of = {p["ticker"]: p["market"] for p in positions}

    out = run(scope="positions", hours=168)
    checked = False
    for event in out["events"]:
        for ticker, reaction in event["price_reaction"].items():
            market = market_of.get(ticker)
            if market is None:
                continue
            snap = snapshot.get([ticker], market).get(ticker)
            expected = bool(snap and snap.get("stale"))
            assert reaction["stale"] == expected
            checked = True
    assert checked, "样本里应该真的核对到至少一只票的新鲜度"
