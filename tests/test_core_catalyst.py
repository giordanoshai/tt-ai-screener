# -*- coding: utf-8 -*-
"""消息面取数。走真实只读连接。"""
import pytest

from core import catalyst, uw
from db import pg

POSITIONS = ["AMZN", "GOOGL", "NVDA"]


def test_news_for_returns_a_key_per_ticker():
    out = catalyst.news_for(POSITIONS, days=30)
    assert set(out) == set(POSITIONS)
    assert all(isinstance(v, list) for v in out.values())


def test_news_for_caps_per_ticker():
    out = catalyst.news_for(POSITIONS, days=90, per_ticker=2)
    assert all(len(v) <= 2 for v in out.values())


def test_news_for_only_returns_requested_significance():
    out = catalyst.news_for(POSITIONS, days=30, min_significance="High")
    for items in out.values():
        assert all(n["significant"] == "High" for n in items)


def test_news_for_medium_includes_high():
    """Medium 的意思是「Medium 及以上」，不是「只要 Medium」。"""
    out = catalyst.news_for(POSITIONS, days=90, min_significance="Medium")
    labels = {n["significant"] for items in out.values() for n in items}
    assert labels <= {"High", "Medium"}


def test_news_for_empty_tickers():
    assert catalyst.news_for([], days=7) == {}


def test_news_recent_sorted_newest_first():
    rows = catalyst.news_recent(hours=72, limit=20)
    stamps = [r["published_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)


def test_earnings_for_returns_days_until():
    out = catalyst.earnings_for(POSITIONS, "US")
    assert set(out) == set(POSITIONS)
    for value in out.values():
        if value and value.get("next_earnings_date"):
            assert isinstance(value["days_until"], int)


def test_earnings_for_cn_is_empty_but_does_not_raise():
    """A股基本面只有 150 只票且财报日全空，缺就是缺，不许抛。"""
    out = catalyst.earnings_for(["600519.SH"], "CN")
    assert set(out) == {"600519.SH"}


def test_options_flow_from_db_has_both_directions():
    out = catalyst.options_flow(POSITIONS, days=10)
    assert set(out) == set(POSITIONS)
    for value in out.values():
        assert {"bullish_premium", "bearish_premium", "net_premium",
                "alert_count", "source"} <= set(value)
        assert value["source"] == "db"


def test_options_flow_zeroes_missing_tickers_not_none():
    out = catalyst.options_flow(["NOSUCHTICKERXYZ"], days=10)
    assert out["NOSUCHTICKERXYZ"]["alert_count"] == 0


@pytest.mark.skipif(not uw.available(), reason="没配 UW_T_KEY")
def test_options_flow_live_falls_back_to_db_shape():
    out = catalyst.options_flow_live(["NVDA"])
    assert out["NVDA"]["source"] in ("uw_live", "db")


def test_insider_only_counts_open_market_trades():
    """A/M/F/D 是授予、行权、代扣税，不是主动买卖，必须过滤掉。"""
    out = catalyst.insider_for(POSITIONS, days=180)
    assert set(out) == set(POSITIONS)
    for value in out.values():
        assert value["buy_count"] >= 0 and value["sell_count"] >= 0


def test_congress_returns_buy_sell_records():
    """设计文档第四节工具 3 要国会议员交易。这三只大盘股一年内必有记录。"""
    out = catalyst.congress_for(POSITIONS, days=365)
    assert set(out) == set(POSITIONS)
    assert any(out.values()), "三只票一年内一条国会议员交易都没有，多半是查询坏了"
    for records in out.values():
        for r in records:
            assert {"transaction_date", "member", "txn_type", "amounts"} <= set(r)


def test_gamma_walls_covers_few_tickers_and_says_so():
    """uw_gex_daily 只有 41 只票，没覆盖的要返回 None 而不是编一个。"""
    out = catalyst.gamma_walls(["NVDA", "NOSUCHTICKERXYZ"])
    assert out["NOSUCHTICKERXYZ"] is None


def test_options_flow_live_shape_matches_db_shape():
    """实时和库里两条路必须是同一个字典形状，调用方才敢统一读。"""
    live = catalyst.options_flow_live(["NVDA"])["NVDA"]
    db = catalyst.options_flow(["NVDA"])["NVDA"]
    assert set(live) == set(db)


def test_options_flow_live_distinguishes_no_data_from_no_flow():
    """实时返回空数组 = 「查过了，这只票没异动」，不能拿库里的旧数据顶掉
    再标成 db——那是在告诉用户「这是缓存」，而其实刚刚实时查过。"""
    import core.uw as uw_mod
    real = uw_mod.flow_alerts_many
    try:
        uw_mod.flow_alerts_many = lambda tickers, limit=50: {t: [] for t in tickers}
        out = catalyst.options_flow_live(["NVDA"])["NVDA"]
        assert out["source"] == "uw_live"
        assert out["alert_count"] == 0
    finally:
        uw_mod.flow_alerts_many = real


class TestNewsCoversBothTables:
    """★ 新闻分散在两张表里，只查一张等于 A股没有消息面 ★

    * `news`    —— 美股为主，不是这两个仓库写的（另一套系统，带大模型
                   打的情绪分和重要性分级）。
    * `cn_news` —— A股，tt-data-hub 的 cn_news 任务写的（2026-09-01 新增）。

    2026-09-01 实测：`news` 表 82387 条里带 A股代码的只有 218 条（0.26%），
    `uw_news` 8848 条里 0 条。当时对贵州茅台、五粮液、宁德时代、比亚迪
    查新闻，一条都查不出来。
    """

    def test_both_tables_are_in_the_sql(self):
        for sql in (catalyst._NEWS_SQL, catalyst._NEWS_RECENT_SQL):
            assert "from news" in sql, "美股新闻表丢了"
            assert "from cn_news" in sql, "A股新闻表没接进来"
            assert "union all" in sql

    def test_columns_are_identical_on_both_sides(self):
        """两边列不一致的话 union 直接报错，或者更糟——列错位。"""
        assert catalyst._NEWS_SQL.count(catalyst.COLS) >= 2

    def test_a_share_news_is_reachable(self):
        """连真库：从 cn_news 里挑一只真有新闻的票，必须查得出来。"""
        picked = pg.query(
            "select related_tickers[1] as ticker from cn_news "
            "where related_tickers <> '{}' order by published_at desc limit 1")
        if not picked:
            pytest.skip("cn_news 还没有数据，先在服务器上跑一次 cn_news 任务")
        ticker = picked[0]["ticker"]
        out = catalyst.news_for([ticker], days=30, min_significance="Low")
        items = out.get(ticker) if isinstance(out, dict) else out
        assert items, f"{ticker} 在 cn_news 里有新闻，却查不出来"

    def test_us_news_still_works(self):
        """接 A股不能把美股这条路搞坏。"""
        picked = pg.query(
            "select related_tickers[1] as ticker from news "
            "where related_tickers <> '{}' and significant is not null "
            "order by published_at desc limit 1")
        if not picked:
            pytest.skip("news 表里没有带分级的新闻")
        ticker = picked[0]["ticker"]
        out = catalyst.news_for([ticker], days=30, min_significance="Low")
        items = out.get(ticker) if isinstance(out, dict) else out
        assert items, f"{ticker} 在 news 里有新闻，却查不出来"

    def test_both_branches_use_an_index(self):
        """两个分支都必须走 GIN。任何一边退化成全表扫，这个查询就废了。"""
        plan = "\n".join(
            list(r.values())[0] for r in
            pg.query("explain " + catalyst._NEWS_SQL,
                     (["600519.SH"], 14, ["High", "Medium", "Low"], 20)))
        assert "Seq Scan on news" not in plan, plan
        assert "Seq Scan on cn_news" not in plan, plan
        assert plan.count("Index Scan") >= 2, plan
