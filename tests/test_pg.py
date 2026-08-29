"""db/pg.py 的测试。

读的部分连真实数据库（只读账号 readonly_user），这是有意的——
这一层的价值就在于它真能连上线上库，用 mock 测等于什么都没测。

写的部分**只用 mock**，一行都不真写库。写账号 write_user 连的是生产数据，
测试里绝不能碰。
"""
import json
import uuid
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

import config
from db import pg


# ---------------------------------------------------------------- 配置


class TestConfig:
    def test_read_url_configured(self):
        """只读连接串必须有值（新键名 PG_READ_URL，或旧键名 READ）。"""
        assert config.PG_READ_URL, "PG_READ_URL / READ 都没配"

    def test_write_url_configured(self):
        assert config.PG_WRITE_URL, "PG_WRITE_URL / WRITE 都没配"

    def test_read_and_write_urls_differ(self):
        """读写必须是两个不同的数据库账号，否则读写分离就是摆设。"""
        assert config.PG_READ_URL != config.PG_WRITE_URL

    def test_watchlist_user_id_is_a_uuid(self):
        """线上 user_watchlist.user_id 是非空 uuid 列，插入时必须带。"""
        uuid.UUID(config.WATCHLIST_USER_ID)

    def test_collection_only_settings_are_gone(self):
        """采集相关配置已经搬到 tt-data-hub，这边不该还留着。"""
        for name in ("DB_PATH", "FINNHUB_KEY", "FINNHUB_TIER", "NEWS_DAYS",
                     "OHLCV_BATCH_SIZE", "FUNDAMENTALS_STALE_DAYS",
                     "TICKERS_FILE", "load_tickers"):
            assert not hasattr(config, name), f"config.{name} 还没删"


# ---------------------------------------------------------------- 只读查询


class TestQuery:
    def test_returns_list_of_dicts(self):
        rows = pg.query("select 1 as a, 'x' as b")
        assert rows == [{"a": 1, "b": "x"}]

    def test_accepts_params(self):
        rows = pg.query("select %s::int as n", (42,))
        assert rows[0]["n"] == 42

    def test_empty_result_is_empty_list(self):
        assert pg.query("select 1 where false") == []

    def test_duckdb_placeholder_is_rejected(self):
        """DuckDB 的 ? 占位符在 psycopg2 里不认，会直接报语法错。

        这是从 DuckDB 移植过来时最容易漏改的一处，这条测试把它钉住。
        """
        with pytest.raises(psycopg2.errors.SyntaxError):
            pg.query("select ? as n")


class TestQueryOne:
    def test_returns_single_dict(self):
        assert pg.query_one("select 7 as n")["n"] == 7

    def test_returns_none_when_empty(self):
        assert pg.query_one("select 1 where false") is None


class TestNumericConversion:
    """Postgres 的 numeric 默认会变成 Decimal，直接丢给 json.dumps 会炸
    （TypeError: Object of type Decimal is not JSON serializable）。
    这一层统一转成 float，上层就不用各自处理了。

    stock_fundamentals 有 20 多个 numeric 列，user_positions 的
    avg_cost / shares 也是 numeric，所以这个转换是必需的。
    """

    def test_numeric_becomes_float(self):
        n = pg.query_one("select 1.5::numeric as n")["n"]
        assert isinstance(n, float)
        assert n == 1.5

    def test_numeric_null_stays_none(self):
        assert pg.query_one("select null::numeric as n")["n"] is None

    def test_numeric_array_becomes_floats(self):
        arr = pg.query_one("select array[1.5, 2.5]::numeric[] as arr")["arr"]
        assert all(isinstance(x, float) for x in arr)

    def test_integer_is_still_int(self):
        assert isinstance(pg.query_one("select 2::int as n")["n"], int)

    def test_real_numeric_columns_are_float(self):
        """真表上验一遍：stock_fundamentals 的 pe_ratio / market_cap 都是 numeric 列。"""
        row = pg.query_one(
            "select pe_ratio, market_cap from stock_fundamentals "
            "where pe_ratio is not null and market_cap is not null "
            "and market = %s limit 1", ("US",))
        assert row is not None, "stock_fundamentals 没数据，先跑 tt-data-hub 的基本面任务"
        assert isinstance(row["pe_ratio"], float)
        assert isinstance(row["market_cap"], float)

    def test_query_result_is_json_serializable(self):
        """这是转 float 的真正目的：web 层和 MCP 层要直接序列化。"""
        rows = pg.query(
            "select ticker, pe_ratio, market_cap from stock_fundamentals "
            "where pe_ratio is not null and market = %s limit 3", ("US",))
        json.dumps(rows)


# ---------------------------------------------------------------- 防误写


class TestReadOnlyIsolation:
    """只读账号写库必须失败——这是防误写的最后一道闸。"""

    def test_read_session_is_marked_read_only(self):
        """连接本身就设成只读事务，比光靠账号权限多一道保险。"""
        assert pg.query_one("show transaction_read_only")["transaction_read_only"] == "on"

    def test_read_connection_cannot_write(self):
        with pytest.raises((psycopg2.errors.ReadOnlySqlTransaction,
                            psycopg2.errors.InsufficientPrivilege)):
            pg.query("insert into ingest_runlog (job) values ('should_fail')")

    def test_read_connection_cannot_write_watchlist(self):
        with pytest.raises((psycopg2.errors.ReadOnlySqlTransaction,
                            psycopg2.errors.InsufficientPrivilege)):
            pg.query("delete from user_watchlist where ticker = '__NEVER__'")


class TestMissingUrl:
    def test_missing_read_url_raises(self, monkeypatch):
        monkeypatch.setattr(config, "PG_READ_URL", "")
        with pytest.raises(RuntimeError, match="PG_READ_URL"):
            pg.query("select 1")

    def test_missing_write_url_raises(self, monkeypatch):
        monkeypatch.setattr(config, "PG_WRITE_URL", "")
        with pytest.raises(RuntimeError, match="PG_WRITE_URL"):
            pg.execute_write("select 1")


# ---------------------------------------------------------------- 写入路径（全 mock）


def _mock_conn(rows=None, rowcount=0):
    cur = MagicMock()
    cur.rowcount = rowcount
    cur.fetchall.return_value = rows or []
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cur
    return connection, cur


class TestWritePath:
    """写入路径一律 mock，绝不真连写账号。"""

    def test_execute_write_uses_write_url(self):
        connection, cur = _mock_conn(rowcount=3)
        with patch("db.pg.psycopg2.connect", return_value=connection) as connect:
            n = pg.execute_write("update user_positions set shares = %s", (10,))
        assert n == 3
        assert connect.call_args.args[0] == config.PG_WRITE_URL
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    def test_query_uses_read_url(self):
        connection, cur = _mock_conn(rows=[{"a": 1}])
        with patch("db.pg.psycopg2.connect", return_value=connection) as connect:
            assert pg.query("select 1 as a") == [{"a": 1}]
        assert connect.call_args.args[0] == config.PG_READ_URL

    def test_fetch_write_returns_rows(self):
        connection, cur = _mock_conn(rows=[{"id": 9}])
        with patch("db.pg.psycopg2.connect", return_value=connection):
            assert pg.fetch_write(
                "insert into user_positions (ticker) values (%s) returning id",
                ("AAPL",)) == [{"id": 9}]
        connection.commit.assert_called_once()

    def test_write_rolls_back_on_error(self):
        connection, cur = _mock_conn()
        cur.execute.side_effect = psycopg2.Error("boom")
        with patch("db.pg.psycopg2.connect", return_value=connection):
            with pytest.raises(psycopg2.Error):
                pg.execute_write("update user_positions set shares = 1")
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        connection.close.assert_called_once()

    def test_write_connection_is_not_read_only(self):
        """写连接不能被设成只读事务，否则写不进去。"""
        connection, cur = _mock_conn()
        with patch("db.pg.psycopg2.connect", return_value=connection):
            pg.execute_write("update user_positions set shares = 1")
        for call in connection.set_session.call_args_list:
            assert call.kwargs.get("readonly") is not True


# ---------------------------------------------------------------- 表结构前提


class TestSchemaExpectations:
    """这些是改造依赖的表结构前提，对不上就别往下做。"""

    @staticmethod
    def _cols(table):
        return {r["column_name"] for r in pg.query(
            "select column_name from information_schema.columns "
            "where table_schema='public' and table_name=%s", (table,))}

    def test_watchlist_uses_created_at_not_added_at(self):
        cols = self._cols("user_watchlist")
        assert "created_at" in cols
        assert "added_at" not in cols
        assert "user_id" in cols

    def test_positions_table_exists_with_market(self):
        cols = self._cols("user_positions")
        assert {"ticker", "market", "avg_cost", "shares", "currency"} <= cols

    def test_stocks_meta_has_sector_column(self):
        assert {"sector", "industry", "market"} <= self._cols("stocks_meta")

    def test_daily_table_has_us_market(self):
        markets = {r["market"] for r in pg.query(
            "select distinct market from stock_ohlcv_daily")}
        assert "US" in markets

    def test_daily_table_has_full_history(self):
        """只读账号确实能读到主表——2026-08 实测 275 万行。"""
        n = pg.query_one("select count(*) as n from stock_ohlcv_daily")["n"]
        assert n > 2_000_000, f"日线只有 {n} 行，数据没同步全"


class TestUpstreamDataNotReadyYet:
    """这两条是数据前提，不是代码问题——tt-data-hub 的对应任务还没跑。

    标成 xfail 是为了不挡住这一层的开发；等上游补数据后它们会变成
    XPASS，看到 XPASS 就把 xfail 标记去掉。
    """

    @pytest.mark.xfail(strict=False,
                       reason="stocks_meta.sector 全空（15661 行 0 个有值），"
                              "要先跑 tt-data-hub 的 meta_refresh")
    def test_stocks_meta_sector_is_populated(self):
        n = pg.query_one(
            "select count(*) as n from stocks_meta where sector is not null")["n"]
        assert n >= 2000, f"只有 {n} 只票有行业分类，先去跑 tt-data-hub 的 meta_refresh"

    @pytest.mark.xfail(strict=False,
                       reason="stock_ohlcv_daily 目前只有 US，"
                              "要先跑 tt-data-hub 的 cn_daily")
    def test_daily_table_has_cn_market(self):
        markets = {r["market"] for r in pg.query(
            "select distinct market from stock_ohlcv_daily")}
        assert "CN" in markets, "A股数据还没进来，先跑 tt-data-hub 的 cn_daily"
