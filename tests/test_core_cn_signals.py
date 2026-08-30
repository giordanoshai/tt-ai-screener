# -*- coding: utf-8 -*-
"""A股独立信号。A股不是美股那套的残缺版，是另一套。"""
import pytest

from core import cn_signals
from db import pg

CN_TICKERS = ["600519.SH", "000001.SZ"]


def test_fund_flow_returns_key_per_ticker():
    out = cn_signals.fund_flow(CN_TICKERS)
    assert set(out) == set(CN_TICKERS)


def test_fund_flow_values_match_the_table():
    """字段不能只判存在。把 net_amount 和 net_5d 接错列，只判键在照样绿。"""
    row = pg.query_one(
        "select ticker, net_amount, net_5d from cn_fund_flow_daily "
        "where snapshot_date = (select max(snapshot_date) from cn_fund_flow_daily) "
        "  and net_5d is not null "
        "order by net_amount desc limit 1")
    out = cn_signals.fund_flow([row["ticker"]])[row["ticker"]]
    assert out["net_amount"] == float(row["net_amount"])
    assert out["net_5d"] == float(row["net_5d"])


def test_fund_flow_amounts_are_in_yuan_not_yi():
    """采集端已经把「1.04亿」和板块的亿元统一换算成元，下游别再换一次。"""
    out = cn_signals.fund_flow(CN_TICKERS)
    values = [v["net_amount"] for v in out.values()
              if v and v.get("net_amount")]
    if values:
        assert max(abs(v) for v in values) > 1000


def test_lhb_returns_real_records_for_a_ticker_that_has_them():
    """原来那条用的两只票在窗口里一条龙虎榜都没有，所以查询写错返回空列表
    它也发现不了。改成从表里现取一只真上过榜的票。"""
    row = pg.query_one(
        "select ticker, count(*) as n from cn_lhb_daily "
        "where list_date >= current_date - 365 "
        "group by ticker order by n desc limit 1")
    if row is None:
        pytest.skip("库里近一年没有龙虎榜数据")
    records = cn_signals.lhb([row["ticker"]], days=365)[row["ticker"]]
    assert len(records) == row["n"]
    assert all({"list_date", "reason", "net_buy"} <= set(r) for r in records)


def test_board_ranking_bottom_is_the_weakest_board():
    """bottom[0] 必须是真正最弱的那个。只判 top[0] >= bottom[0] 的话，
    倒序切片写错（bottom 变成倒数第十）它照样绿。"""
    out = cn_signals.board_ranking()
    assert out["top"][0]["rank"] == 1
    assert out["bottom"][0]["rank"] == out["total_boards"]


def test_board_ranking_ranks_are_distinct_and_complete():
    """rank 全写死成 1 的话，len(all_ranks) == total_boards 照样成立——
    而 rank 正是 board_of 和 score_short_cn 拿来分档的字段。"""
    out = cn_signals.board_ranking()
    assert sorted(out["all_ranks"].values()) == list(
        range(1, out["total_boards"] + 1))


def test_board_ranking_rejects_bad_type():
    with pytest.raises(ValueError):
        cn_signals.board_ranking("sector")


def test_board_of_is_mostly_none_and_that_is_expected():
    """A股缺个股→行业映射，绝大多数票拿不到板块。这是已知缺口，不是 bug。"""
    out = cn_signals.board_of(CN_TICKERS)
    assert set(out) == set(CN_TICKERS)
    assert cn_signals.BOARD_MAPPING_GAP


def test_board_of_finds_the_board_for_a_limit_up_ticker():
    """board_of「查到了」那条分支之前零覆盖——样本票都是不涨停的大盘股，
    走的全是返回 None 那条路。这里从涨停表现取一只能对上板块的票。"""
    row = pg.query_one(
        "select l.ticker from cn_limit_pool_daily l "
        "join cn_board_flow_daily b "
        "  on b.board_name = l.industry "
        " and b.board_type = 'industry' "
        " and b.snapshot_date = l.snapshot_date "
        "where l.snapshot_date = "
        "      (select max(snapshot_date) from cn_limit_pool_daily) "
        "limit 1")
    if row is None:
        pytest.skip("当日没有能对上板块的涨停票")
    got = cn_signals.board_of([row["ticker"]])[row["ticker"]]
    assert got is not None
    assert got["board_name"]
    assert 1 <= got["rank"] <= got["total_boards"]


def test_limit_sentiment_values_match_the_table():
    """只判键集合的话，把 limit_up_count 和 broken_count 对调也发现不了。"""
    row = pg.query_one(
        "select count(*) as n, "
        "       count(*) filter (where open_times > 0) as broken, "
        "       max(limit_streak) as streak "
        "from cn_limit_pool_daily "
        "where snapshot_date = "
        "      (select max(snapshot_date) from cn_limit_pool_daily)")
    out = cn_signals.limit_sentiment()
    assert out["limit_up_count"] == row["n"]
    assert out["broken_count"] == row["broken"]
    assert out["max_streak"] == row["streak"]
    assert out["broken_rate"] == round(row["broken"] / row["n"], 3)


def test_signal_dates_reports_each_table_and_whether_they_agree():
    """四张表各自的最新日期要能被问出来，齐不齐要有明确结论。"""
    out = cn_signals.signal_dates()
    assert {"fund_flow", "board_flow", "limit_pool", "lhb",
            "in_sync", "gap"} <= set(out)
    same = {out["fund_flow"], out["board_flow"], out["limit_pool"]}
    assert out["in_sync"] == (len({d for d in same if d is not None}) <= 1)
    assert (out["gap"] is None) == out["in_sync"]


def test_empty_input_everywhere():
    assert cn_signals.fund_flow([]) == {}
    assert cn_signals.lhb([]) == {}
    assert cn_signals.board_of([]) == {}
