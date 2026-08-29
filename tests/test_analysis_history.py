# -*- coding: utf-8 -*-
"""分析历史：跑完 AI 分析要把结果存下来，历史页要能查出来。

来龙去脉：`analysis_history` 这张表原来在本地 DuckDB 里，迁到自建 Postgres
时漏掉了，于是写入被注释掉、两个查询端点改成返回空列表，历史页永远显示
「暂无记录」。2026-08-29 用户确认这个功能要保留，表已经在线上建好了
（见 `sql/001_analysis_history.sql`），这里把功能接回来。

**这是本项目唯一一条新增的写库路径。** 在此之前只写自选和持仓。所以：

* 写失败绝不能让分析本身失败 —— 分析结果已经返回给用户了，存历史存不上
  只该记一条日志。
* 写路径优先用 mock 测。只有一个用例真跑往返，用一眼看得出是测试的值，
  try/finally 删掉，最后断言行数回到原值。**绝不碰那三条迁进来的历史数据。**
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from db import pg

MODEL_CFG = {
    "id": "fake-model", "display_name": "假模型", "api_base": "https://fake.invalid",
    "api_key": "", "model_id": "fake-1", "api_format": "openai",
    "role": "analysis", "supports_thinking": False,
}


@pytest.fixture
def client():
    from web.app import app
    return TestClient(app)


@pytest.fixture
def fake_analysis(monkeypatch):
    """把 AI 调用和取数都换成假的，只留下「存历史」这条路真跑。"""
    import ai_client
    import skills

    monkeypatch.setattr(ai_client, "get_model", lambda **kw: dict(MODEL_CFG))
    monkeypatch.setattr(ai_client, "_chat", lambda *a, **kw: "分析正文（测试）")
    monkeypatch.setattr(ai_client, "chat_stream", lambda *a, **kw: iter([
        {"type": "content", "chunk": "分析"},
        {"type": "content", "chunk": "正文（测试）"},
    ]))
    monkeypatch.setattr(skills, "gather_context",
                        lambda skill, params=None: '{"candidates": []}')


# ---------------------------------------------------------------- 写入

class TestAnalysisGetsSaved:

    def test_analyze_writes_one_history_row(self, client, fake_analysis):
        with patch("web.app.pg.execute_write", return_value=1) as write:
            resp = client.post("/analyze", json={
                "skill": "swing_screen", "tickers": ["AAPL", "MSFT"]})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert write.call_count == 1, "跑完分析应该往 analysis_history 写一行"

        sql, params = write.call_args[0]
        assert "insert into analysis_history" in sql.lower()
        assert "?" not in sql, "占位符是 %s，不是 DuckDB 的 ?"
        assert "%s" in sql
        assert params[0] == "swing_screen"
        assert params[1] == "AAPL,MSFT"
        assert params[2] == 2
        assert "分析正文（测试）" in params

    def test_model_name_is_recorded(self, client, fake_analysis):
        with patch("web.app.pg.execute_write", return_value=1) as write:
            client.post("/analyze", json={"skill": "swing_screen",
                                          "tickers": ["AAPL"]})
        assert "假模型" in write.call_args[0][1]

    def test_position_review_records_its_tickers(self, client, fake_analysis):
        with patch("web.app.pg.execute_write", return_value=1) as write:
            client.post("/analyze", json={
                "skill": "position_review",
                "positions": [{"ticker": "AAPL", "cost_basis": 1, "shares": 1},
                              {"ticker": "NVDA", "cost_basis": 2, "shares": 2}]})
        params = write.call_args[0][1]
        assert params[1] == "AAPL,NVDA"
        assert params[2] == 2

    def test_write_failure_does_not_break_the_analysis(self, client, fake_analysis):
        """分析结果已经算出来了，存不进历史不该让用户拿不到结果。"""
        with patch("web.app.pg.execute_write", side_effect=RuntimeError("库挂了")):
            resp = client.post("/analyze", json={"skill": "swing_screen",
                                                 "tickers": ["AAPL"]})
        assert resp.status_code == 200
        assert resp.json()["analysis"] == "分析正文（测试）"

    def test_stream_endpoint_also_saves(self, client, fake_analysis):
        """网页上点「分析」走的是 /analyze/stream，这条不存历史就等于没做。"""
        with patch("web.app.pg.execute_write", return_value=1) as write:
            resp = client.post("/analyze/stream", json={
                "skill": "swing_screen", "tickers": ["AAPL"]})
            body = resp.text
        assert "分析" in body
        assert write.call_count == 1
        assert "分析正文（测试）" in write.call_args[0][1]

    def test_stream_saves_nothing_when_model_produced_nothing(
            self, client, fake_analysis, monkeypatch):
        import ai_client
        monkeypatch.setattr(ai_client, "chat_stream", lambda *a, **kw: iter([]))
        with patch("web.app.pg.execute_write", return_value=1) as write:
            client.post("/analyze/stream", json={"skill": "swing_screen",
                                                 "tickers": ["AAPL"]})
        assert write.call_count == 0, "空结果不该留一条空历史"

    def test_nothing_else_gets_written(self, client, fake_analysis):
        """写库只允许发生在 analysis_history 上，别顺手动了持仓或自选。"""
        with patch("web.app.pg.execute_write", return_value=1) as write, \
             patch("web.app.pg.fetch_write") as fetch_write:
            client.post("/analyze", json={"skill": "swing_screen",
                                          "tickers": ["AAPL"]})
        assert fetch_write.call_count == 0
        sql = write.call_args[0][0].lower()
        assert "user_positions" not in sql
        assert "user_watchlist" not in sql


# ---------------------------------------------------------------- 读取

FAKE_ROWS = [
    {"id": 9001, "created_at": "2026-08-29T10:00:00+00:00", "skill": "swing_screen",
     "tickers": "AAPL,MSFT", "ticker_count": 2, "analysis_text": "正文一",
     "model": "假模型"},
    {"id": 9000, "created_at": "2026-08-28T10:00:00+00:00", "skill": "position_review",
     "tickers": "NVDA", "ticker_count": 1, "analysis_text": "正文二",
     "model": "假模型"},
]


class TestHistoryEndpoints:

    def test_list_returns_rows_from_the_table(self, client):
        with patch("web.app.pg.query", return_value=FAKE_ROWS) as query:
            data = client.get("/analyze/history").json()
        assert [h["id"] for h in data["history"]] == [9001, 9000]
        assert data["history"][0]["analysis_text"] == "正文一"
        sql = query.call_args[0][0].lower()
        assert "from analysis_history" in sql
        assert "order by" in sql and "desc" in sql

    def test_list_can_filter_by_skill(self, client):
        with patch("web.app.pg.query", return_value=[]) as query:
            client.get("/analyze/history?skill=swing_screen")
        sql, params = query.call_args[0]
        assert "skill" in sql.lower()
        assert "swing_screen" in str(params)

    def test_unknown_skill_filter_is_ignored_not_injected(self, client):
        with patch("web.app.pg.query", return_value=[]) as query:
            resp = client.get("/analyze/history?skill=drop%20table")
        assert resp.status_code == 200
        assert "drop table" not in str(query.call_args[0][1])

    def test_limit_is_clamped(self, client):
        with patch("web.app.pg.query", return_value=[]) as query:
            client.get("/analyze/history?limit=100000")
        assert query.call_args[0][1]["limit"] <= 200

    def test_list_survives_a_db_outage(self, client):
        with patch("web.app.pg.query", side_effect=RuntimeError("库挂了")):
            resp = client.get("/analyze/history")
        assert resp.status_code == 200
        assert resp.json()["history"] == []
        assert resp.json().get("error")

    def test_detail_returns_the_full_record(self, client):
        row = dict(FAKE_ROWS[0], context_json='{"candidates": []}')
        with patch("web.app.pg.query_one", return_value=row):
            data = client.get("/analyze/history/9001").json()
        assert data["id"] == 9001
        assert data["analysis_text"] == "正文一"
        assert data["context_json"] == '{"candidates": []}'

    def test_detail_404_when_missing(self, client):
        with patch("web.app.pg.query_one", return_value=None):
            assert client.get("/analyze/history/424242").status_code == 404

    def test_list_reads_the_real_table(self, client):
        """真库上跑一遍。迁进来的三条历史数据必须查得到。"""
        data = client.get("/analyze/history?limit=100").json()
        assert not data.get("error"), data.get("error")
        assert len(data["history"]) >= 3
        first = data["history"][0]
        for key in ("id", "created_at", "skill", "tickers",
                    "ticker_count", "analysis_text", "model"):
            assert key in first, f"历史页要的字段少了 {key}"
        # 日期必须已经序列化成字符串，否则 JSONResponse 直接抛
        assert isinstance(first["created_at"], str)

    def test_detail_reads_the_real_table(self, client):
        rows = client.get("/analyze/history?limit=100").json()["history"]
        one = client.get(f"/analyze/history/{rows[-1]['id']}").json()
        assert one["id"] == rows[-1]["id"]
        assert one["analysis_text"]


# ---------------------------------------------------------------- 真跑一次往返

MARKER = "【自动化测试写入，可以删】test_analysis_history round trip"


def test_real_round_trip_then_clean_up():
    """真写一行、查出来、删掉，最后行数回到原值。

    用的值一眼就能看出是测试写的；无论中间怎么失败都在 finally 里删。
    """
    from web import app as web_app

    before = pg.query_one("select count(*) as c from analysis_history")["c"]
    written = None
    try:
        ok = web_app._save_analysis_history(
            "swing_screen",
            {"tickers": ["__TEST__"], "context": '{"test": true}'},
            MARKER, "测试模型")
        assert ok is True

        rows = pg.query(
            "select id, skill, tickers, ticker_count, context_json, model "
            "from analysis_history where analysis_text = %s", (MARKER,))
        assert len(rows) == 1
        written = rows[0]["id"]
        assert rows[0]["skill"] == "swing_screen"
        assert rows[0]["tickers"] == "__TEST__"
        assert rows[0]["ticker_count"] == 1
        assert rows[0]["context_json"] == '{"test": true}'
        assert rows[0]["model"] == "测试模型"
    finally:
        pg.execute_write(
            "delete from analysis_history where analysis_text = %s", (MARKER,))

    after = pg.query_one("select count(*) as c from analysis_history")["c"]
    assert after == before, "清理没做干净"
    assert written is not None
    assert pg.query_one("select id from analysis_history where id = %s",
                        (written,)) is None


def test_the_migrated_history_is_still_there():
    """三条迁进来的真实历史数据不该被任何测试碰掉。"""
    rows = pg.query("select id from analysis_history where id in (1, 2, 3)")
    assert len(rows) == 3
