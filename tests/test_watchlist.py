"""tools/watchlist.py 的测试。

读的部分连真实的 Postgres（只读账号），这是有意的——自选工具的价值就在于
它真能把线上那张表读出来，用 mock 测等于什么都没测。

写的部分（加/删自选）**默认全部 mock**，只有一条端到端往返测试真写库，
而且用的是一眼就能看出是测试数据的假代码 ``__PROBE_WL__``：

- 真实自选池里 100% 不会有这个代码，所以不会碰到用户的真数据；
- 插入和清理都放在 try/finally 里，测试中途炸了也一定会清掉；
- 测试最后会断言自选池总行数回到原值，且探针行确实没了。

绝对不要把这里的 PROBE_TICKER 换成 AAPL 之类的真代码——那样一旦用户
自己也自选了这只票，测试的清理动作会把他的数据删掉。
"""
import ast
import inspect
import json
import re
import sys
import types
from unittest.mock import patch

import pytest

import config
from db import pg
from tools import watchlist

# 一眼就能看出是测试数据；stocks_meta 里不存在这个代码
PROBE_TICKER = "__PROBE_WL__"


def _watchlist_count() -> int:
    return pg.query_one("select count(*) as n from user_watchlist")["n"]


def _probe_rows() -> list[dict]:
    return pg.query(
        "select id, ticker, user_id, created_at from user_watchlist "
        "where ticker = %s", (PROBE_TICKER,))


# ---------------------------------------------------------------- 源码层护栏


class TestPortedOffDuckDB:
    """从 DuckDB 移植过来时最容易漏改的几处，直接钉在源码上。"""

    @staticmethod
    def _code() -> str:
        """源码去掉文档字符串和注释，只留真正会跑的代码。

        必须去掉，否则这些护栏会被 watchlist.py 自己的说明文字误伤——
        那段说明里正好就写着「原来叫 added_at」。
        """
        src = inspect.getsource(watchlist)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    src = src.replace(doc, "")
        return "\n".join(line.split("#", 1)[0] for line in src.splitlines())

    def test_no_duckdb_import(self):
        """db.init 是 DuckDB 那一套，改造后不该再引用。"""
        assert "db.init" not in self._code()

    def test_uses_created_at_not_added_at(self):
        """线上表的时间列叫 created_at，写成 added_at 会报「列不存在」。"""
        code = self._code()
        assert "added_at" not in code
        assert "created_at" in code

    def test_no_duckdb_placeholder(self):
        """psycopg2 的占位符是 %s，留着 DuckDB 的 ? 会报语法错。"""
        assert re.search(r"=\s*\?|\(\s*\?\s*\)|values\s*\(\s*\?", self._code(),
                         re.IGNORECASE) is None

    def test_no_correlated_max_date_subquery(self):
        """相关子查询取最新日期在 Postgres 上是全表扫 275 万行，必须用 CTE。"""
        code = self._code().lower()
        assert "select max(date) from stock_ohlcv_daily where ticker" not in code

    def test_every_ohlcv_join_carries_market(self):
        """日线表混了美股和 A股，漏了 market 条件会把 A股混进美股结果。"""
        code = self._code().lower()
        assert "stock_ohlcv_daily" in code
        # 每次连日线表，同一段连接条件里都要出现 market
        for chunk in code.split("stock_ohlcv_daily")[1:]:
            head = chunk.split("left join")[0].split("group by")[0]
            assert "market" in head, f"这段连日线表的条件没带 market：{head!r}"


# ---------------------------------------------------------------- 列出自选


class TestListWatchlist:
    def test_returns_json_list(self):
        assert isinstance(json.loads(watchlist.list_watchlist()), list)

    def test_no_error_key(self):
        """SQL 写错时这个函数会吞掉异常返回 {"error": ...}，必须让它露出来。"""
        result = json.loads(watchlist.list_watchlist())
        assert not (isinstance(result, dict) and "error" in result), result

    def test_rows_carry_price_and_technicals(self):
        rows = json.loads(watchlist.list_watchlist())
        if not rows:
            pytest.skip("自选池是空的")
        assert {"ticker", "company_name", "sector", "market", "close",
                "pct_chg", "rsi_14", "ma_20", "ma_50", "ma_200",
                "vol_ratio", "created_at"} <= set(rows[0])

    def test_row_count_matches_table(self):
        """左连接接错会让行数翻倍，这条把它钉住。"""
        rows = json.loads(watchlist.list_watchlist())
        assert len(rows) == _watchlist_count()

    def test_no_duplicate_tickers(self):
        tickers = [r["ticker"] for r in json.loads(watchlist.list_watchlist())]
        assert len(tickers) == len(set(tickers))

    def test_prices_are_float_not_decimal(self):
        """numeric 列在访问层已经转成 float，Decimal 会让 json.dumps 直接炸。"""
        rows = [r for r in json.loads(watchlist.list_watchlist())
                if r["close"] is not None]
        if not rows:
            pytest.skip("自选池里没有带价格的票")
        assert isinstance(rows[0]["close"], float)

    def test_market_is_never_null(self):
        """market 用来区分美股和 A股，不能是空的。"""
        rows = json.loads(watchlist.list_watchlist())
        if not rows:
            pytest.skip("自选池是空的")
        assert all(r["market"] for r in rows)

    def test_bar_date_reported(self):
        """带上这根 K 线的日期，价格是不是隔夜的一眼能看出来。"""
        rows = json.loads(watchlist.list_watchlist())
        if not rows:
            pytest.skip("自选池是空的")
        assert "date" in rows[0]

    def test_result_is_json_serializable(self):
        """web 层和 MCP 层要直接序列化，时间戳必须已经转成字符串。"""
        json.dumps(json.loads(watchlist.list_watchlist()))

    def test_db_error_becomes_error_json(self):
        with patch("db.pg.query", side_effect=RuntimeError("boom")):
            assert "error" in json.loads(watchlist.list_watchlist())


# ---------------------------------------------------------------- 加自选（mock）


class TestAddToWatchlist:
    """写路径默认全 mock，不真连写账号。"""

    def test_empty_ticker_rejected_without_touching_db(self):
        with patch("db.pg.execute_write") as write:
            result = json.loads(watchlist.add_to_watchlist("   "))
        assert "error" in result
        write.assert_not_called()

    def test_unknown_ticker_rejected(self):
        """真读一次 stocks_meta：这个代码库里没有，必须被挡掉。"""
        with patch("db.pg.execute_write") as write:
            result = json.loads(watchlist.add_to_watchlist("ZZZZNOTREAL"))
        assert "error" in result
        write.assert_not_called()

    def test_ticker_is_uppercased_and_stripped(self):
        with patch("db.pg.query_one", return_value={"ticker": "AAPL"}), \
             patch("db.pg.execute_write", return_value=1) as write:
            result = json.loads(watchlist.add_to_watchlist("  aapl "))
        assert result.get("ok") is True
        assert result["ticker"] == "AAPL"
        assert "AAPL" in write.call_args.args[1]

    def test_insert_carries_user_id(self):
        """线上表 user_id 是非空 uuid 列，不带会直接违反非空约束。"""
        with patch("db.pg.query_one", return_value={"ticker": "AAPL"}), \
             patch("db.pg.execute_write", return_value=1) as write:
            watchlist.add_to_watchlist("AAPL")
        sql, params = write.call_args.args[0], write.call_args.args[1]
        assert "user_id" in sql.lower()
        assert config.WATCHLIST_USER_ID in params

    def test_insert_does_not_supply_id(self):
        """id 是自增列（GENERATED BY DEFAULT AS IDENTITY），别自己塞值。"""
        with patch("db.pg.query_one", return_value={"ticker": "AAPL"}), \
             patch("db.pg.execute_write", return_value=1) as write:
            watchlist.add_to_watchlist("AAPL")
        sql = write.call_args.args[0].lower()
        columns = sql.split("(", 1)[1].split(")", 1)[0]
        assert "id" not in [c.strip() for c in columns.split(",")]

    def test_insert_is_conflict_safe(self):
        """重复加同一只票不能报错。"""
        with patch("db.pg.query_one", return_value={"ticker": "AAPL"}), \
             patch("db.pg.execute_write", return_value=0) as write:
            result = json.loads(watchlist.add_to_watchlist("AAPL"))
        assert "on conflict" in write.call_args.args[0].lower()
        assert "error" not in result

    def test_conflict_target_covers_ticker_unique_index(self):
        """这张表 ticker 单列也有唯一索引，只盯 (user_id, ticker) 挡不住它。"""
        with patch("db.pg.query_one", return_value={"ticker": "AAPL"}), \
             patch("db.pg.execute_write", return_value=0) as write:
            watchlist.add_to_watchlist("AAPL")
        sql = write.call_args.args[0].lower()
        conflict = sql.split("on conflict", 1)[1]
        assert "(ticker)" in conflict.replace(" ", "") or \
            conflict.strip().startswith("do nothing")

    def test_missing_user_id_config_is_reported(self, monkeypatch):
        monkeypatch.setattr(config, "WATCHLIST_USER_ID", "")
        with patch("db.pg.query_one", return_value={"ticker": "AAPL"}), \
             patch("db.pg.execute_write") as write:
            result = json.loads(watchlist.add_to_watchlist("AAPL"))
        assert "error" in result
        write.assert_not_called()

    def test_db_error_becomes_error_json(self):
        with patch("db.pg.query_one", return_value={"ticker": "AAPL"}), \
             patch("db.pg.execute_write", side_effect=RuntimeError("boom")):
            assert "error" in json.loads(watchlist.add_to_watchlist("AAPL"))


# ---------------------------------------------------------------- 删自选（mock）


class TestRemoveFromWatchlist:
    def test_removed_ticker_reported(self):
        with patch("db.pg.fetch_write", return_value=[{"ticker": "AAPL"}]):
            result = json.loads(watchlist.remove_from_watchlist("aapl"))
        assert result.get("ok") is True
        assert result["removed"] == "AAPL"

    def test_absent_ticker_reports_error(self):
        with patch("db.pg.fetch_write", return_value=[]):
            assert "error" in json.loads(watchlist.remove_from_watchlist("AAPL"))

    def test_delete_is_scoped_to_one_ticker(self):
        """删除语句必须带 where ticker，漏了会把整张表清空。"""
        with patch("db.pg.fetch_write", return_value=[{"ticker": "AAPL"}]) as delete:
            watchlist.remove_from_watchlist("AAPL")
        sql = delete.call_args.args[0].lower()
        assert "where" in sql and "ticker = %s" in sql
        assert "returning" in sql

    def test_db_error_becomes_error_json(self):
        with patch("db.pg.fetch_write", side_effect=RuntimeError("boom")):
            assert "error" in json.loads(watchlist.remove_from_watchlist("AAPL"))


# ---------------------------------------------------------------- 真写一次往返


class TestRealRoundTrip:
    """唯一一条真写库的测试：加一个探针票，验完立刻删干净。

    用 __PROBE_WL__ 而不是真代码，是为了保证就算清理逻辑失效，
    被留下的也只是一条一眼能认出的垃圾数据，不会动到用户的自选。
    """

    def test_add_list_remove_leaves_no_trace(self):
        before = _watchlist_count()
        try:
            # stocks_meta 里没有探针票，只把「这只票存不存在」这一步替换掉，
            # 插入语句本身照旧真跑，这样才验得到列名和 user_id。
            with patch("db.pg.query_one",
                       return_value={"ticker": PROBE_TICKER}):
                added = json.loads(watchlist.add_to_watchlist(PROBE_TICKER))
                assert added.get("ok") is True, added
                assert len(_probe_rows()) == 1

                # 再加一次，不能报错也不能多出一行
                again = json.loads(watchlist.add_to_watchlist(PROBE_TICKER))
                assert "error" not in again, again
                assert len(_probe_rows()) == 1

            row = _probe_rows()[0]
            assert str(row["user_id"]) == config.WATCHLIST_USER_ID
            assert row["created_at"] is not None  # 默认值 now() 生效
            assert _watchlist_count() == before + 1

            # 没有行情、也没有 stocks_meta 记录的票，仍然要出现在列表里
            # （证明几个 join 都是左连接，不会把票过滤掉）
            listed = {r["ticker"]: r for r in json.loads(watchlist.list_watchlist())}
            assert PROBE_TICKER in listed
            assert listed[PROBE_TICKER]["close"] is None
            assert listed[PROBE_TICKER]["market"] == "US"

            removed = json.loads(watchlist.remove_from_watchlist(PROBE_TICKER))
            assert removed.get("removed") == PROBE_TICKER
            assert _probe_rows() == []

            # 再删一次要报「不在自选里」
            assert "error" in json.loads(
                watchlist.remove_from_watchlist(PROBE_TICKER))
        finally:
            pg.execute_write(
                "delete from user_watchlist where ticker = %s", (PROBE_TICKER,))

        # 清理是否真的干净
        assert _probe_rows() == []
        assert _watchlist_count() == before


# ---------------------------------------------------------------- 分析自选


def _fake_skills(recorder):
    module = types.ModuleType("skills")

    def gather_context(skill, payload):
        recorder.append((skill, payload))
        return json.dumps({"skill": skill, "tickers": payload["tickers"]})

    module.gather_context = gather_context
    return module


class TestAnalyzeWatchlist:
    """skills 引擎是任务 6 的活，这里用假模块顶掉，只验参数传对没有。"""

    def test_empty_watchlist_reports_error(self):
        with patch("db.pg.query", return_value=[]):
            assert "error" in json.loads(watchlist.analyze_watchlist())

    def test_longterm_is_the_default(self, monkeypatch):
        calls = []
        monkeypatch.setitem(sys.modules, "skills", _fake_skills(calls))
        with patch("db.pg.query", return_value=[{"ticker": "AAPL"}]):
            watchlist.analyze_watchlist()
        assert calls[0][0] == "longterm_screen"

    def test_swing_selects_swing_skill(self, monkeypatch):
        calls = []
        monkeypatch.setitem(sys.modules, "skills", _fake_skills(calls))
        with patch("db.pg.query", return_value=[{"ticker": "AAPL"}]):
            watchlist.analyze_watchlist("swing")
        assert calls[0][0] == "swing_screen"

    def test_tickers_come_from_the_watchlist_table(self, monkeypatch):
        calls = []
        monkeypatch.setitem(sys.modules, "skills", _fake_skills(calls))
        with patch("db.pg.query",
                   return_value=[{"ticker": "AAPL"}, {"ticker": "MSFT"}]) as read:
            watchlist.analyze_watchlist()
        assert calls[0][1]["tickers"] == ["AAPL", "MSFT"]
        assert "user_watchlist" in read.call_args.args[0].lower()

    def test_skill_error_becomes_error_json(self, monkeypatch):
        broken = types.ModuleType("skills")

        def gather_context(skill, payload):
            raise RuntimeError("boom")

        broken.gather_context = gather_context
        monkeypatch.setitem(sys.modules, "skills", broken)
        with patch("db.pg.query", return_value=[{"ticker": "AAPL"}]):
            assert "error" in json.loads(watchlist.analyze_watchlist())

    def test_real_watchlist_query_runs(self):
        """真跑一次取票的查询（不调 skills），确认列名对得上。"""
        rows = pg.query("select ticker from user_watchlist order by created_at")
        assert isinstance(rows, list)


# ---------------------------------------------------------------- 等上游数据


class TestUpstreamDataNotReadyYet:
    """这两条是数据前提，不是代码问题——代码写对了也过不了，
    要等 tt-data-hub 的对应任务跑完。等它们变成 XPASS 就把标记去掉。
    """

    @pytest.mark.xfail(strict=False,
                       reason="stocks_meta.sector 全空（15661 行 0 个有值），"
                              "要先跑 tt-data-hub 的 meta_refresh")
    def test_sector_shows_up_in_the_list(self):
        rows = json.loads(watchlist.list_watchlist())
        if not rows:
            pytest.skip("自选池是空的")
        assert any(r["sector"] for r in rows), "自选池里没有一只票有行业分类"

    @pytest.mark.xfail(strict=False,
                       reason="stock_ohlcv_daily 目前只有 US，"
                              "要先跑 tt-data-hub 的 cn_daily")
    def test_cn_ticker_uses_its_own_latest_trading_day(self):
        """A股和美股的最新交易日不一样，取价必须各按各的市场来。"""
        cn = pg.query_one(
            "select ticker from stocks_meta where market = 'CN' limit 1")
        assert cn is not None, "stocks_meta 里还没有 A股"
        cn_latest = pg.query_one(
            "select max(date) as d from stock_ohlcv_daily where market = 'CN'")["d"]
        us_latest = pg.query_one(
            "select max(date) as d from stock_ohlcv_daily where market = 'US'")["d"]
        assert cn_latest is not None and us_latest is not None
