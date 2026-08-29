# -*- coding: utf-8 -*-
"""个股详情端点 /ticker/{ticker}，外加首页那两个只读状态端点的测试。

原来这个文件 mock 的是 ``web.app.get_conn``，而且第二个用例还 mock 了
``data_sources.registry.ProviderRegistry`` —— 那是老版本「库里查不到就现场
去采一份、顺手写回库」的行为。采集已经整个搬到 tt-data-hub，这个项目现在是
只读消费端，所以那条回落路径必须消失：查不到就老老实实说查不到，绝不写库。
下面 test_ticker_detail_never_writes 就是钉住这一条的。

线上 news 表跟 DuckDB 那版长得完全不一样，是这次改动最容易翻车的地方：
标题列叫 ``title`` 不叫 ``headline``、正文列叫 ``content`` 不叫 ``summary``、
而且**没有 ticker 列**，关联的股票放在 ``related_tickers`` 这个文本数组里
（上面建了 GIN 索引，所以要用数组运算符 ``&&`` 才吃得到索引）。
"""
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

META_ROW = {
    "company_name": "Apple Inc",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "market": "US",
    "currency": "USD",
    "latest_price": 195.0,
}

FUND_ROW = {
    "pe_ratio": 28.5, "ps_ratio": 7.2, "pb_ratio": 45.0, "peg_ratio": 1.5,
    "market_cap": 3.0e12, "revenue_growth_yoy": 0.125,
    "earnings_growth_yoy": 0.15, "gross_margin": 0.46, "roe": 1.6,
    "fcf_yield": 0.035, "analyst_rating": "buy",
    "analyst_target_price": 240.0, "analyst_count": 42,
    "next_earnings_date": date(2026, 10, 30),
    "last_earnings_date": date(2026, 7, 31), "mspr": -12.5,
    "updated_at": date(2026, 8, 28),
}

NEWS_ROWS = [{
    "headline": "Apple beats expectations",
    "summary": "苹果财报超预期",
    "source": "reuters",
    "url": "https://example.invalid/1",
    "published_at": "2026-08-28T10:00:00+00:00",
    "sentiment_label": "positive",
    "sentiment_score": 0.8,
}]


@pytest.fixture
def client():
    from web.app import app
    return TestClient(app)


def _fake_db(meta=META_ROW, fund=FUND_ROW, news=NEWS_ROWS, recorder=None):
    """按 SQL 内容分发的假数据库，不依赖调用顺序。"""
    def query_one(sql, params=None):
        if recorder is not None:
            recorder.append((sql, params))
        if "stocks_meta" in sql:
            return dict(meta) if meta else None
        if "stock_fundamentals" in sql:
            return dict(fund) if fund else None
        raise AssertionError(f"没预料到的 query_one: {sql}")

    def query(sql, params=None):
        if recorder is not None:
            recorder.append((sql, params))
        if "news" in sql:
            return [dict(r) for r in news]
        raise AssertionError(f"没预料到的 query: {sql}")

    return query_one, query


def test_ticker_detail_from_db(client):
    query_one, query = _fake_db()
    with patch("web.app.pg.query_one", side_effect=query_one), \
         patch("web.app.pg.query", side_effect=query):
        resp = client.get("/ticker/aapl")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticker"] == "AAPL"
    assert data["company_name"] == "Apple Inc"
    assert data["market"] == "US"
    assert data["currency"] == "USD"
    assert data["fundamentals"]["pe_ratio"] == 28.5
    assert data["fundamentals"]["mspr"] == -12.5
    assert len(data["news"]) == 1
    # 前端 screener.html 读的是 n.headline，别把列名原样漏过去
    assert data["news"][0]["headline"] == "Apple beats expectations"


def test_ticker_detail_no_data(client):
    query_one, query = _fake_db(meta=None, fund=None, news=[])
    with patch("web.app.pg.query_one", side_effect=query_one), \
         patch("web.app.pg.query", side_effect=query):
        resp = client.get("/ticker/FAKE")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticker"] == "FAKE"
    assert data["fundamentals"] is None
    assert data["news"] == []
    assert data["company_name"] == ""


def test_ticker_detail_never_writes(client):
    """只读消费端：查不到也不许现场去采、更不许写回库。"""
    query_one, query = _fake_db(meta=None, fund=None, news=[])
    with patch("web.app.pg.query_one", side_effect=query_one), \
         patch("web.app.pg.query", side_effect=query), \
         patch("web.app.pg.execute_write") as w1, \
         patch("web.app.pg.fetch_write") as w2:
        client.get("/ticker/FAKE")
    w1.assert_not_called()
    w2.assert_not_called()


def test_fundamentals_query_takes_only_the_latest_row(client):
    """主键是 (ticker, updated_at)，2135 只票摊了 29943 行；不去重会重复十几遍。"""
    seen = []
    query_one, query = _fake_db(recorder=seen)
    with patch("web.app.pg.query_one", side_effect=query_one), \
         patch("web.app.pg.query", side_effect=query):
        client.get("/ticker/AAPL")
    fund_sql = next(s for s, _ in seen if "stock_fundamentals" in s)
    assert "order by updated_at desc" in fund_sql.lower()
    assert "limit 1" in fund_sql.lower()


def test_news_query_uses_related_tickers_array(client):
    """news 表没有 ticker 列、也没有 headline 列。

    票在 related_tickers 这个文本数组里（GIN 索引），写成 ``where ticker = %s``
    直接报「列不存在」。用重叠运算符 && 查，跟 skills/__init__.py 里那份保持一致。
    标题列叫 title，前端读的是 headline，所以必须在 SQL 里改名。
    """
    seen = []
    query_one, query = _fake_db(recorder=seen)
    with patch("web.app.pg.query_one", side_effect=query_one), \
         patch("web.app.pg.query", side_effect=query):
        client.get("/ticker/AAPL")
    news_sql, news_params = next((s, p) for s, p in seen if "from news" in s)
    assert "related_tickers &&" in news_sql
    assert "ticker = " not in news_sql
    assert "as headline" in news_sql
    assert ["AAPL"] in list(news_params)


def test_ticker_detail_uses_psycopg_placeholders(client):
    seen = []
    query_one, query = _fake_db(recorder=seen)
    with patch("web.app.pg.query_one", side_effect=query_one), \
         patch("web.app.pg.query", side_effect=query):
        client.get("/ticker/AAPL")
    for sql, _ in seen:
        assert "?" not in sql


def test_ticker_detail_reports_error_instead_of_500(client):
    with patch("web.app.pg.query_one", side_effect=RuntimeError("boom")):
        resp = client.get("/ticker/AAPL")
    assert resp.status_code == 200
    assert "boom" in resp.json()["error"]


# ── 首页的只读状态端点 ─────────────────────────────────────────────────────────

def test_status_is_per_market(client):
    """两个市场的最新交易日、票数要分开报，不能混成一个数。"""
    def fake_query_one(sql, params=None):
        if "stock_ohlcv_daily" in sql:
            return {"c": 1500}
        if "from news" in sql:
            return {"c": 81996, "latest": date(2026, 8, 29)}
        if "user_watchlist" in sql:
            return {"c": 153}
        raise AssertionError(sql)

    with patch("web.app.latest_trading_day", return_value=date(2026, 8, 28)), \
         patch("web.app.pg.query_one", side_effect=fake_query_one):
        resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["markets"]["US"]["latest"] == "2026-08-28"
    assert data["markets"]["CN"]["tickers"] == 1500
    # 老模板还在读这几个扁平字段，别把它们弄丢
    assert data["ohlcv_latest"] == "2026-08-28"
    assert data["tickers"] == 153


def test_status_survives_db_outage(client):
    with patch("web.app.latest_trading_day", side_effect=RuntimeError("down")), \
         patch("web.app.pg.query_one", side_effect=RuntimeError("down")):
        resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json()["ohlcv_latest"] == "—"


def test_last_update_reads_data_hub_runlog(client):
    """update_history 表没了；采集进度现在看 tt-data-hub 写的 ingest_runlog。"""
    row = {
        "job": "us_daily", "market": "US", "status": "ok",
        "started_at": "2026-08-29T06:00:00+00:00",
        "finished_at": "2026-08-29T06:06:25+00:00",
        "rows_written": 12345, "tickers_total": 2164, "tickers_failed": 3,
        "error": None,
    }
    with patch("web.app.pg.query", return_value=[row]) as q:
        resp = client.get("/api/update/last")
    sql = q.call_args.args[0]
    assert "ingest_runlog" in sql
    data = resp.json()
    assert data["status"] == "ok"
    assert data["job"] == "us_daily"
    assert data["duration_s"] == 385
