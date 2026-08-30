# -*- coding: utf-8 -*-
"""突发消息侦测。已经涨完的利好不是机会，是风险。"""
import json

from tools import detect_catalysts as dc


def run(**kwargs):
    return json.loads(dc.detect_catalysts(**kwargs))


def test_positions_scope_only_covers_held_names():
    out = run(scope="positions", hours=168)
    assert all(e["held"] for e in out["events"])


def test_every_event_says_whether_price_already_reacted():
    out = run(scope="market", hours=48)
    for event in out["events"]:
        assert "price_reaction" in event
        assert "reacted" in event["price_reaction"]


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
    """排序键现在是「持仓 -> 自选 -> 是不是大盘综述 -> 重要性 -> 反应过没
    有」，market_wide 插在重要性前面（修复轮 1：大盘综述不能顶到个股专属
    消息前面）。"""
    out = run(scope="market", hours=72)
    keys = [(not e["held"], not e["watched"], e["market_wide"],
             e["_significance_rank"], e["price_reaction"]["reacted"] is True)
            for e in out["events"] if "_significance_rank" in e]
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
    market_wide 比对。"""
    from db import pg

    out = run(scope="market", hours=72, min_significance="Low")
    news_events = [e for e in out["events"] if e["source_table"] == "news"]
    assert news_events, "样本里应该有 news 表来源的事件，不然这条测试测不出东西"

    checked_wide = checked_narrow = False
    for event in news_events:
        row = pg.query_one(
            "select array_length(related_tickers, 1) as n from news "
            "where coalesce(title_cn, title) = %s and published_at = %s",
            (event["event"], event["published_at"]))
        if row is None or row["n"] is None:
            continue
        expected = dc.is_market_wide(row["n"])
        assert event["market_wide"] == expected, (
            f"{event['event'][:30]!r} 挂了 {row['n']} 只票，"
            f"market_wide 应该是 {expected}，实际是 {event['market_wide']}")
        if expected:
            checked_wide = True
        else:
            checked_narrow = True
    assert checked_wide, "样本里应该真的核对到一条被判成大盘综述的新闻"
    assert checked_narrow, "样本里应该真的核对到一条被判成个股专属的新闻"


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
        for key in ("pct_chg", "vol_ratio", "atr_pct"):
            value = event["price_reaction"][key]
            if value is not None:
                checked = True
                assert value == round(value, 2), (
                    f"{key} 没有四舍五入到 2 位小数：{value}")
    assert checked, "样本里应该真的有非空的价格数字，不然这条测试测不出东西"


def test_price_reaction_states_which_day_the_move_is_from():
    """reacted 只看最新交易日一天的涨跌，字段里要带上具体是哪一天
    （as_of），不能让人以为是消息发布以来的累计涨跌。"""
    out = run(scope="positions", hours=168)
    checked = False
    for event in out["events"]:
        if event["price_reaction"]["reacted"] is not None:
            checked = True
            assert event["price_reaction"]["as_of"] is not None
    assert checked, "样本里应该真的有算出反应结果的事件"


def test_watchlist_scope_marks_watched():
    out = run(scope="watchlist", hours=168)
    assert all(e["watched"] or e["held"] for e in out["events"])


def test_merges_both_news_sources():
    out = run(scope="market", hours=72)
    sources = {e["source_table"] for e in out["events"]}
    assert sources <= {"news", "uw_news"}


def test_bad_scope_returns_error():
    assert "error" in run(scope="everything")


def test_no_events_is_a_sentence_not_an_empty_blob():
    out = run(scope="positions", hours=1)
    if not out["events"]:
        assert out["message"]
