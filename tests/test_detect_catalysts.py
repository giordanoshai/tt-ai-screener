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
    out = run(scope="market", hours=72)
    keys = [(not e["held"], not e["watched"],
             e["_significance_rank"], e["price_reaction"]["reacted"] is True)
            for e in out["events"] if "_significance_rank" in e]
    assert keys == sorted(keys)


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
