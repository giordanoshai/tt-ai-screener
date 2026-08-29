# -*- coding: utf-8 -*-
"""网页层的测试：/screen 端点，以及那几个「改成直接调 tools/」的端点。

原来这个文件 mock 的是 ``web.app.get_conn``（本地 DuckDB 文件）。数据库换成
只读 Postgres 之后 get_conn 整个不存在了，所以全部重写。重写时盯住两件事：

1. **/screen 仍然自己写查询**，因为筛选页支持的条件是 MCP 工具 screen_stocks
   的超集（同时站上 50 和 200 日线、多头排列、离 20 日线的距离区间、放量倍数、
   逼近 20 日新高、振幅、涨跌幅区间、条数上限 100）。所以这里要逐条钉住真库上
   踩出来的那几个坑：查询必须带市场条件、最新交易日必须是先单独查好再当常量
   传进来、占位符必须是 %s、行业分类要有兜底。
2. **自选和持仓的端点不再自己写 SQL**，改成调 tools/ 里那四个已经测过的函数。
   所以这几个测试断言的是「有没有把参数原样交给那个函数」，而不是 SQL 长什么样。
"""
import json
import re
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

APP_SOURCE = Path(__file__).parent.parent / "web" / "app.py"

# /screen 主查询返回的一行，字段名跟前端 screener.html 用的对上
SCREEN_ROW = {
    "ticker": "AAPL",
    "company_name": "Apple Inc",
    "sector": "Technology",
    "_sector_from_meta": True,
    "market": "US",
    "close": 195.0,
    "pct_chg": 1.5,
    "rsi_14": 55.0,
    "vol_ratio": 1.2,
    "dist_ma20_pct": 0.02,
    "atr_pct": 0.018,
    "is_core": True,
}


@pytest.fixture
def client():
    from web.app import app
    return TestClient(app)


@pytest.fixture
def screen_db():
    """把 /screen 用到的两次数据库往返都接管掉，并把主查询的 SQL 和参数录下来。"""
    calls = []

    def fake_query(sql, params=None):
        calls.append((sql, params))
        return [dict(SCREEN_ROW)]

    with patch("web.app.latest_trading_day", return_value=date(2026, 8, 28)) as day, \
         patch("web.app.pg.query", side_effect=fake_query):
        yield {"calls": calls, "latest_day": day}


# ── /screen ───────────────────────────────────────────────────────────────────

def test_screen_returns_list(client, screen_db):
    resp = client.post("/screen", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["ticker"] == "AAPL"
    assert data["total"] == 1
    assert data["date"] == "2026-08-28"
    assert data["market"] == "US"
    # 内部字段不该漏给前端
    assert "_sector_from_meta" not in data["results"][0]


def test_screen_scopes_query_to_one_market(client, screen_db):
    """日线表混了 275 万行美股和 105 万行 A股，漏掉市场条件会串市场。"""
    client.post("/screen", json={"market": "CN"})
    screen_db["latest_day"].assert_called_once_with("CN")
    sql, params = screen_db["calls"][0]
    assert "o.market = %(market)s" in sql
    assert params["market"] == "CN"


def test_screen_takes_latest_day_as_a_constant(client, screen_db):
    """最新交易日先单独查好再当常量传进来，主查询里不许再算一次。

    真库实测：相关子查询 6750ms，两段式 109ms。
    """
    client.post("/screen", json={})
    sql, params = screen_db["calls"][0]
    assert "o.date = %(as_of)s" in sql
    assert params["as_of"] == date(2026, 8, 28)
    assert "max(date)" not in sql.lower()


def test_screen_uses_psycopg_placeholders(client, screen_db):
    """占位符是 %s 不是 DuckDB 的 ?，写错会报 syntax error at or near "?"。"""
    client.post("/screen", json={
        "rsi_min": 40, "rsi_max": 70, "vol_ratio_min": 1.5,
        "dist_ma20_min": -0.05, "dist_ma20_max": 0.1,
        "atr_pct_min": 0.02, "pct_chg_min": -3, "pct_chg_max": 8,
        "sectors": ["Technology"], "limit": 25,
    })
    sql, _ = screen_db["calls"][0]
    assert "?" not in sql


def test_screen_sector_filter_accepts_alias_spellings(client, screen_db):
    """两个数据源的行业叫法不一样，Technology 得同时认 Information Technology。"""
    client.post("/screen", json={"sectors": ["Technology"]})
    sql, params = screen_db["calls"][0]
    assert "coalesce(m.sector, ts.sector)" in sql
    assert "technology" in params["sectors"]
    assert "informationtechnology" in params["sectors"]


def test_screen_sector_has_fallback_source(client, screen_db):
    """stocks_meta.sector 只有 1555 只有值，得拿 ticker_over20b 兜底。"""
    client.post("/screen", json={"sectors": ["Healthcare"]})
    sql, _ = screen_db["calls"][0]
    assert "ticker_over20b" in sql
    assert "distinct on (ticker)" in sql
    # 兜底表主键是 (id, ticker)，不去重会让一只票连出多行
    resp = client.post("/screen", json={"sectors": ["Healthcare"]})
    assert "sector_source" in resp.json()


def test_screen_tier_core_means_in_watchlist(client, screen_db):
    client.post("/screen", json={"tier": "core"})
    sql, _ = screen_db["calls"][0]
    assert "w.ticker is not null" in sql

    screen_db["calls"].clear()
    client.post("/screen", json={"tier": "universe"})
    sql, _ = screen_db["calls"][0]
    assert "w.ticker is null" in sql


def test_screen_market_without_data_short_circuits(client):
    with patch("web.app.latest_trading_day", return_value=None), \
         patch("web.app.pg.query") as q:
        resp = client.post("/screen", json={"market": "CN"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert data["total"] == 0
    assert data["date"] is None
    q.assert_not_called()


def test_screen_rejects_unknown_market(client):
    with patch("web.app.latest_trading_day") as day:
        resp = client.post("/screen", json={"market": "all"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert "error" in body
    day.assert_not_called()


def test_screen_caps_limit(client, screen_db):
    client.post("/screen", json={"limit": 5000})
    _, params = screen_db["calls"][0]
    assert params["limit"] <= 200


def test_screen_reports_db_error_instead_of_500(client):
    with patch("web.app.latest_trading_day", return_value=date(2026, 8, 28)), \
         patch("web.app.pg.query", side_effect=RuntimeError("boom")):
        resp = client.post("/screen", json={})
    assert resp.status_code == 200
    assert "boom" in resp.json()["error"]


# ── 改成调 tools/ 的端点 ───────────────────────────────────────────────────────

def test_positions_endpoint_delegates_to_tool(client):
    payload = json.dumps({
        "positions": [
            {"ticker": "AAPL", "market": "US", "currency": "USD",
             "avg_cost": 100.0, "shares": 10, "close": 195.0,
             "pnl_pct": 95.0, "pnl_amount": 950.0},
            {"ticker": "600519.SH", "market": "CN", "currency": "CNY",
             "avg_cost": 1500.0, "shares": 100, "close": 1600.0,
             "pnl_pct": 6.67, "pnl_amount": 10000.0},
        ],
        "summary": {
            "USD": {"count": 1, "total_value": 1950.0, "total_cost": 1000.0,
                    "total_pnl": 950.0, "total_pnl_pct": 95.0},
            "CNY": {"count": 1, "total_value": 160000.0, "total_cost": 150000.0,
                    "total_pnl": 10000.0, "total_pnl_pct": 6.67},
        },
    })
    with patch("web.app.positions_tool.list_positions", return_value=payload) as fn:
        resp = client.get("/api/positions")
    fn.assert_called_once_with()
    data = resp.json()
    assert [p["ticker"] for p in data["positions"]] == ["AAPL", "600519.SH"]
    # 美元和人民币绝不能加到一起，汇总必须是按币种拆开的
    assert set(data["summary"]) == {"USD", "CNY"}
    assert data["positions"][0]["market_value"] == 1950.0
    assert data["positions"][1]["currency"] == "CNY"


def test_positions_endpoint_passes_tool_error_through(client):
    with patch("web.app.positions_tool.list_positions",
               return_value=json.dumps({"error": "连不上库"})):
        resp = client.get("/api/positions")
    assert resp.json()["positions"] == []
    assert "连不上库" in resp.json()["error"]


def test_position_add_delegates_to_tool(client):
    with patch("web.app.positions_tool.add_position",
               return_value=json.dumps({"ok": True, "ticker": "AAPL"})) as fn:
        resp = client.post("/api/positions/add",
                           data={"ticker": " aapl ", "avg_cost": "100.5",
                                 "shares": "10"})
    fn.assert_called_once_with("AAPL", 100.5, 10.0, "US")
    assert resp.json()["ok"] is True


def test_position_add_supports_cn_market(client):
    with patch("web.app.positions_tool.add_position",
               return_value=json.dumps({"ok": True})) as fn:
        client.post("/api/positions/add",
                    data={"ticker": "600519.SH", "avg_cost": "1500",
                          "shares": "100", "market": "cn"})
    fn.assert_called_once_with("600519.SH", 1500.0, 100.0, "CN")


def test_position_remove_delegates_to_tool(client):
    with patch("web.app.positions_tool.remove_position",
               return_value=json.dumps({"ok": True, "removed": "AAPL"})) as fn:
        resp = client.post("/api/positions/remove", data={"ticker": "aapl"})
    fn.assert_called_once_with("AAPL")
    assert resp.json()["ok"] is True


def test_ticker_add_delegates_to_watchlist_tool(client):
    with patch("web.app.watchlist_tool.add_to_watchlist",
               return_value=json.dumps({"ok": True, "ticker": "AAPL"})) as fn:
        resp = client.post("/ticker/add", data={"ticker": "aapl, msft nvda"})
    assert [c.args[0] for c in fn.call_args_list] == ["AAPL", "MSFT", "NVDA"]
    assert resp.json()["ok"] is True


def test_ticker_add_reports_unknown_ticker(client):
    with patch("web.app.watchlist_tool.add_to_watchlist",
               return_value=json.dumps({"error": "ZZZZ 不在数据库里"})):
        resp = client.post("/ticker/add", data={"ticker": "ZZZZ"})
    body = resp.json()
    assert body["ok"] is False
    assert "不在数据库里" in body["error"]


def test_ticker_remove_delegates_to_watchlist_tool(client):
    with patch("web.app.watchlist_tool.remove_from_watchlist",
               return_value=json.dumps({"ok": True, "removed": "AAPL"})) as fn:
        resp = client.post("/ticker/remove", data={"ticker": "aapl"})
    fn.assert_called_once_with("AAPL")
    assert resp.json()["ok"] is True


# ── 采集端点已经搬到 tt-data-hub ───────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("post", "/update"),
    ("get", "/update/status"),
    ("get", "/update/stream"),
])
def test_collection_endpoints_are_gone(client, method, path):
    """这三个端点先是改成 410 存根，2026-08-29 连存根一起删了。

    理由：410 存根在页面上还是三个能点的按钮，点了弹一句「已经搬走了」——
    看着像坏了。采集在这个项目里已经彻底不存在，按钮和路由一起没了才诚实。
    """
    assert getattr(client, method)(path).status_code in (404, 405)


# ── 静态检查：DuckDB 的痕迹一点都不许剩 ────────────────────────────────────────

def test_source_has_no_duckdb_leftovers():
    """跟验收用的那条 grep 一模一样，钉住不要哪天又被抄回来。"""
    src = APP_SOURCE.read_text(encoding="utf-8")
    for banned in ("get_conn", "init_db", "db.init", "db.update",
                   "data_sources", "yfinance", "duckdb"):
        assert banned not in src, f"web/app.py 里还留着 {banned}"
    # DuckDB 风格的位置占位符：?, 或 ?)
    assert not re.search(r"\?\s*[,)]", src), "web/app.py 里还有 ? 占位符"
