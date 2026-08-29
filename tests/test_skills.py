# -*- coding: utf-8 -*-
"""skills/__init__.py 的测试。

这个模块是 `analyze_watchlist` 和 `analyze_positions` 两个 MCP 工具的取数层。
它们是**在函数体里** `from skills import gather_context` 的，所以模块坏掉的时候
import 阶段不报错，要等真调用才炸，然后被外面的 except 吞成
`{"error": ...}`——从工具返回值上根本看不出是这儿坏了。所以这里的测试全部
直接打在 skills 自己身上。

读的部分连真实的 Postgres（只读账号），跟 test_watchlist.py 一个路子：
用 mock 测取数层等于什么都没测。这个模块**一行都不写库**，所以没有写测试。
"""
import ast
import inspect
import json
import re

import pytest

import skills

# 测试用的真实代码。美股用 AAPL/MSFT，A股用茅台——都确认在库里有日线。
US_TICKERS = ["AAPL", "MSFT"]
CN_TICKER = "600519.SH"


# ---------------------------------------------------------------- 源码层护栏


class TestPortedOffDuckDB:
    """从 DuckDB 移植到 Postgres 时最容易漏改的几处，直接钉在源码上。"""

    @staticmethod
    def _code() -> str:
        """源码去掉文档字符串和注释，只留真正会跑的代码。

        必须去掉，否则这些护栏会被模块自己的说明文字误伤——说明里正好
        写着「原来用的是 ? 占位符」这类句子。
        """
        src = inspect.getsource(skills)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    src = src.replace(doc, "")
        return "\n".join(line.split("#", 1)[0] for line in src.splitlines())

    def test_no_duckdb_import(self):
        """db.init 是 DuckDB 那一套，采集搬走之后不该再引用。"""
        code = self._code()
        assert "db.init" not in code
        assert "get_conn" not in code

    def test_no_duckdb_placeholder(self):
        """psycopg2 的占位符是 %s，留着 DuckDB 的 ? 会报 syntax error。"""
        assert re.search(r"=\s*\?|\(\s*\?\s*\)|in\s*\(\s*\{placeholders\}",
                         self._code(), re.IGNORECASE) is None

    def test_no_correlated_max_date_subquery(self):
        """相关子查询取最新日期在 Postgres 上是全表扫 275 万行。"""
        code = self._code().lower()
        assert "(select max(date) from stock_ohlcv_daily)" not in code
        assert "select max(date) from stock_ohlcv_daily where ticker" not in code

    def test_every_ohlcv_query_carries_market(self):
        """日线表混了美股和 A股，漏了 market 会把 A股串进美股结果。"""
        code = self._code().lower()
        assert "stock_ohlcv_daily" in code
        for chunk in code.split("stock_ohlcv_daily")[1:]:
            head = chunk.split("order by")[0].split(") ")[0]
            assert "market" in head, f"这段查日线表的条件没带 market：{head!r}"

    def test_fundamentals_deduped(self):
        """stock_fundamentals 一只票有十几行（主键是 ticker+updated_at），
        不去重会让筛选结果全是重复行。"""
        assert "distinct on (ticker)" in self._code().lower()

    def test_news_uses_real_columns(self):
        """线上 news 表没有 ticker 列也没有 headline 列：
        是 related_tickers（text[]，有 GIN 索引）和 title。"""
        code = self._code().lower()
        assert "from news" in code
        for chunk in code.split("from news")[1:]:
            head = chunk.split("order by")[0]
            assert "related_tickers" in head, f"没用 related_tickers：{head!r}"
            assert re.search(r"\bticker\s*=", head) is None, \
                f"news 表没有 ticker 列：{head!r}"


# ---------------------------------------------------------------- 语言和提示词


class TestPrompts:
    def test_get_lang_returns_supported(self):
        assert skills.get_lang() in skills.SUPPORTED_LANGS

    @pytest.mark.parametrize("name", skills.SKILL_NAMES)
    def test_load_skill_prompt_non_empty(self, name):
        assert len(skills.load_skill_prompt(name)) > 100

    @pytest.mark.parametrize("name", skills.SKILL_NAMES)
    def test_load_default_skill_prompt_non_empty(self, name):
        assert len(skills.load_default_skill_prompt(name)) > 100

    def test_unknown_skill_rejected(self):
        with pytest.raises(ValueError):
            skills.load_skill_prompt("no_such_skill")


# ---------------------------------------------------------------- 按代码取数


class TestQueryTickersData:
    def test_returns_row_per_ticker(self):
        rows = skills._query_tickers_data(US_TICKERS)
        assert {r["ticker"] for r in rows} == set(US_TICKERS)

    def test_carries_price_and_technicals(self):
        rows = skills._query_tickers_data(["AAPL"])
        row = rows[0]
        assert row["close"] and row["close"] > 0
        assert row["ma_50"] is not None
        assert row["company_name"]

    def test_empty_input(self):
        assert skills._query_tickers_data([]) == []

    def test_unknown_ticker_does_not_vanish_silently(self):
        """自选池是用户自己维护的，票在库里没数据也得出现在结果里，
        不能被数据缺失悄悄吞掉。"""
        rows = skills._query_tickers_data(["AAPL", "__NOPE__"])
        assert {r["ticker"] for r in rows} == {"AAPL", "__NOPE__"}

    def test_cn_ticker_gets_cn_data(self):
        rows = skills._query_tickers_data([CN_TICKER])
        assert len(rows) == 1
        assert rows[0]["close"] and rows[0]["close"] > 0

    def test_mixed_markets_do_not_leak(self):
        """美股和 A股一起传进来，两边都要有数据，且不串行。"""
        rows = skills._query_tickers_data(["AAPL", CN_TICKER])
        by_ticker = {r["ticker"]: r for r in rows}
        assert set(by_ticker) == {"AAPL", CN_TICKER}
        assert by_ticker["AAPL"]["close"] != by_ticker[CN_TICKER]["close"]


class TestQueryNews:
    def test_returns_bucket_per_ticker(self):
        news = skills._query_news_for_tickers(US_TICKERS, days=30)
        assert set(news) >= set(US_TICKERS)

    def test_entries_use_headline_key(self):
        """技能提示词里约定的字段名就是 headline，改名会让提示词对不上。"""
        news = skills._query_news_for_tickers(["AAPL"], days=30)
        items = news["AAPL"]
        assert items, "AAPL 近 30 天应该有新闻"
        assert set(items[0]) == {"headline", "sentiment_label",
                                 "sentiment_score", "published_at"}
        assert items[0]["headline"]

    def test_empty_input(self):
        assert skills._query_news_for_tickers([]) == {}


class TestQueryPositionData:
    def test_us_position(self):
        data = skills._query_position_data("AAPL", "US")
        assert data["ticker"] == "AAPL"
        assert data["close"] and data["close"] > 0
        assert data["company_name"]
        assert isinstance(data["news"], list)

    def test_cn_position(self):
        data = skills._query_position_data(CN_TICKER, "CN")
        assert data["close"] and data["close"] > 0

    def test_market_inferred_from_suffix(self):
        """web 那边调用时不传 market，得能从代码后缀认出来。"""
        assert skills._query_position_data(CN_TICKER)["close"] > 0


class TestBuiltinScreens:
    def test_longterm_candidates_run(self):
        rows = skills._query_longterm_candidates()
        assert isinstance(rows, list)
        for row in rows:
            assert row["close"] is not None

    def test_swing_candidates_run(self):
        rows = skills._query_swing_candidates()
        assert isinstance(rows, list)
        for row in rows:
            assert row["close"] > row["ma_20"]


# ---------------------------------------------------------------- 对外入口


class TestGatherContext:
    def test_longterm_with_tickers(self):
        out = json.loads(skills.gather_context(
            "longterm_screen", {"tickers": US_TICKERS}))
        assert out["candidates_count"] == len(US_TICKERS)
        assert "recent_news" in out["candidates"][0]

    def test_swing_with_tickers(self):
        out = json.loads(skills.gather_context(
            "swing_screen", {"tickers": ["AAPL"]}))
        assert out["candidates"][0]["ticker"] == "AAPL"

    def test_longterm_builtin(self):
        out = json.loads(skills.gather_context("longterm_screen"))
        assert "candidates" in out

    def test_swing_builtin(self):
        out = json.loads(skills.gather_context("swing_screen"))
        assert "candidates" in out

    def test_position_review(self):
        out = json.loads(skills.gather_context("position_review", {
            "positions": [{"ticker": "AAPL", "market": "US",
                           "cost_basis": 100.0, "shares": 10}],
        }))
        assert out["positions_count"] == 1
        position = out["positions"][0]
        assert position["cost_basis"] == 100.0
        assert position["close"] > 0

    def test_position_review_passes_through_uw_fields(self):
        out = json.loads(skills.gather_context("position_review", {
            "positions": [{"ticker": "AAPL", "market": "US",
                           "uw_flow_score": 7}],
        }))
        assert out["positions"][0]["uw_flow_score"] == 7

    def test_position_review_needs_positions(self):
        out = json.loads(skills.gather_context("position_review", {}))
        assert "error" in out

    def test_unknown_skill(self):
        with pytest.raises(ValueError):
            skills.gather_context("no_such_skill")

    def test_output_is_json_serializable(self):
        """日期和 numeric 直接扔进 json.dumps 会抛异常，必须都能落地。"""
        raw = skills.gather_context("position_review", {
            "positions": [{"ticker": CN_TICKER, "market": "CN"}]})
        assert json.loads(raw)["positions_count"] == 1
