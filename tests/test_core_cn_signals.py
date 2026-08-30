# -*- coding: utf-8 -*-
"""A股独立信号。A股不是美股那套的残缺版，是另一套。"""
import pytest

from core import cn_signals

CN_TICKERS = ["600519.SH", "000001.SZ"]


def test_fund_flow_returns_key_per_ticker():
    out = cn_signals.fund_flow(CN_TICKERS)
    assert set(out) == set(CN_TICKERS)


def test_fund_flow_has_today_and_five_day_net():
    out = cn_signals.fund_flow(CN_TICKERS)
    for value in out.values():
        if value is not None:
            assert "net_amount" in value and "net_5d" in value


def test_fund_flow_amounts_are_in_yuan_not_yi():
    """采集端已经把「1.04亿」和板块的亿元统一换算成元，下游别再换一次。"""
    out = cn_signals.fund_flow(CN_TICKERS)
    values = [v["net_amount"] for v in out.values()
              if v and v.get("net_amount")]
    if values:
        assert max(abs(v) for v in values) > 1000


def test_lhb_returns_list_per_ticker():
    out = cn_signals.lhb(CN_TICKERS, days=180)
    assert set(out) == set(CN_TICKERS)
    assert all(isinstance(v, list) for v in out.values())


def test_board_ranking_gives_top_and_bottom():
    out = cn_signals.board_ranking()
    assert out["total_boards"] > 50
    assert len(out["top"]) == 10
    assert len(out["bottom"]) == 10
    assert out["top"][0]["net_amount"] >= out["bottom"][0]["net_amount"]


def test_board_ranking_exposes_full_rank_map():
    out = cn_signals.board_ranking()
    assert len(out["all_ranks"]) == out["total_boards"]


def test_board_ranking_rejects_bad_type():
    with pytest.raises(ValueError):
        cn_signals.board_ranking("sector")


def test_board_of_is_mostly_none_and_that_is_expected():
    """A股缺个股→行业映射，绝大多数票拿不到板块。这是已知缺口，不是 bug。"""
    out = cn_signals.board_of(CN_TICKERS)
    assert set(out) == set(CN_TICKERS)
    assert cn_signals.BOARD_MAPPING_GAP


def test_limit_sentiment_shape():
    out = cn_signals.limit_sentiment()
    assert {"limit_up_count", "broken_count", "broken_rate",
            "max_streak", "date"} <= set(out)


def test_empty_input_everywhere():
    assert cn_signals.fund_flow([]) == {}
    assert cn_signals.lhb([]) == {}
    assert cn_signals.board_of([]) == {}
