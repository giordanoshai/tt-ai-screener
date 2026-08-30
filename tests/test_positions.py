"""tools/positions.py 的测试。

这次改造的重点是**分币种**：用户同时做美股和 A股，美股按美元计价、
A股按人民币计价，两边加在一起的数字没有任何意义，所以汇总必须按币种拆开。

测试怎么分工：

* **校验和汇总的逻辑**——纯 Python，不连库，用假数据把两种币种都测透。
  A股真数据现在还没进库（``stock_ohlcv_daily`` 只有 US），假数据是唯一
  能把分币种逻辑测全的办法。
* **写库路径**——一律 mock，不真写。写连接连的是生产数据。
* **读库路径**——连真库（只读账号），因为这一层的价值就在于 SQL 真能跑通：
  列名写错、占位符写成 DuckDB 的 ``?``、查询计划退化成全表扫，
  这几种错只有真跑一遍才发现得了。
* **一次真实往返**——只用 ``__PROBE_POS__`` 这个一眼看得出是测试的代码，
  放在 try/finally 里，跑完断言行数回到原值。绝不碰真实持仓。
"""
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from db import pg
from tools import positions

# 一眼看得出是测试数据的代码，绝不会跟真实持仓撞车
PROBE = "__PROBE_POS__"


@contextmanager
def _patch_pg(**mocks):
    """把 db.pg 上的若干个函数换成假的，并把这些假函数交出来给测试断言。

    直接用 patch.multiple 的话，只有传 DEFAULT 哨兵时它才会把 mock 塞进
    返回的字典；传了现成的 mock 对象反而拿不到，所以这里自己包一层。
    """
    with patch.multiple("db.pg", **mocks):
        yield mocks


def _row(ticker, currency, close, avg_cost, shares, market=None):
    """造一行 list_positions 查询结果，用来测汇总逻辑。"""
    return {
        "ticker": ticker,
        "market": market or ("US" if currency == "USD" else "CN"),
        "currency": currency,
        "company_name": ticker,
        "sector": None,
        "avg_cost": avg_cost,
        "shares": shares,
        "close": close,
        "pnl_pct": None,
        "pnl_amount": None,
        "rsi_14": None,
        "ma_50": None,
        "ma_200": None,
        "as_of": None,
        "updated_at": None,
    }


# ------------------------------------------------------------ 参数校验


class TestAddPositionValidation:
    """参数不对的时候必须在碰数据库之前就挡回去。"""

    @staticmethod
    def _no_db():
        """把数据库访问层整个换成假的，被调到就说明校验漏了。"""
        return _patch_pg(query_one=MagicMock(), query=MagicMock(),
                         execute_write=MagicMock(), fetch_write=MagicMock())

    def test_rejects_blank_ticker(self):
        with self._no_db() as mocks:
            assert "error" in json.loads(positions.add_position("   ", 10.0, 1))
            mocks["execute_write"].assert_not_called()

    def test_rejects_unknown_market(self):
        with self._no_db() as mocks:
            result = json.loads(positions.add_position("AAPL", 10.0, 1, market="HK"))
            assert "error" in result
            mocks["execute_write"].assert_not_called()

    def test_rejects_non_positive_cost(self):
        with self._no_db() as mocks:
            assert "error" in json.loads(positions.add_position("AAPL", 0, 1))
            mocks["execute_write"].assert_not_called()

    def test_rejects_non_positive_shares(self):
        with self._no_db() as mocks:
            assert "error" in json.loads(positions.add_position("AAPL", 10.0, 0))
            mocks["execute_write"].assert_not_called()


# ------------------------------------------------------------ 写入路径（全 mock）


class TestAddPositionWrite:
    """写库路径一律 mock。这里盯的是「写进去的 SQL 和参数对不对」。"""

    @staticmethod
    def _patched(meta_found=True):
        known = {"ticker": "X"} if meta_found else None
        return _patch_pg(
            # 第一次 query_one 查 stocks_meta，第二次查已有持仓（没有）
            query_one=MagicMock(side_effect=[known, None]),
            execute_write=MagicMock(return_value=1))

    def test_us_position_gets_usd(self):
        with self._patched() as mocks:
            result = json.loads(positions.add_position("aapl", 200.0, 10))
        assert result.get("ok") is True
        assert result["ticker"] == "AAPL"      # 代码要转大写
        assert result["market"] == "US"
        assert result["currency"] == "USD"
        params = mocks["execute_write"].call_args.args[1]
        assert "USD" in params and "US" in params

    def test_cn_position_gets_cny(self):
        with self._patched() as mocks:
            result = json.loads(
                positions.add_position("600519.SH", 1300.0, 100, market="CN"))
        assert result.get("ok") is True
        assert result["market"] == "CN"
        assert result["currency"] == "CNY"
        params = mocks["execute_write"].call_args.args[1]
        assert "CNY" in params and "CN" in params

    def test_upsert_conflict_target_is_ticker_only(self):
        """user_positions 的主键只有 ticker 一列。

        写成 ``on conflict (ticker, market)`` 会直接报
        ``no unique or exclusion constraint matching the ON CONFLICT
        specification``——这条测试就是把这个坑钉死。
        """
        with self._patched() as mocks:
            positions.add_position("AAPL", 200.0, 10)
        sql = mocks["execute_write"].call_args.args[0].lower()
        assert "on conflict (ticker)" in sql
        assert "(ticker, market)" not in sql

    def test_sql_uses_psycopg_placeholders(self):
        """占位符必须是 %s。写成 DuckDB 的 ? 会报 syntax error at or near "?"。"""
        with self._patched() as mocks:
            positions.add_position("AAPL", 200.0, 10)
        sql = mocks["execute_write"].call_args.args[0]
        assert "%s" in sql
        assert "?" not in sql

    def test_meta_lookup_is_market_scoped(self):
        """查这只票存不存在也要带 market，不然 A股 US 混着查。"""
        with self._patched() as mocks:
            positions.add_position("AAPL", 200.0, 10, market="US")
        params = mocks["query_one"].call_args_list[0].args[1]
        assert "AAPL" in params and "US" in params

    def test_unknown_ticker_is_rejected_without_writing(self):
        with self._patched(meta_found=False) as mocks:
            result = json.loads(positions.add_position("ZZZZNOTREAL", 1.0, 1))
        assert "error" in result
        mocks["execute_write"].assert_not_called()

    def test_switching_market_on_same_code_is_flagged(self):
        """主键只有 ticker，所以同一个代码换市场会把原来那笔顶掉。

        实际撞车概率接近零（AAPL vs 600519.SH 格式完全不同），
        但真发生了要让用户看见，不能悄悄覆盖。
        """
        with _patch_pg(
                query_one=MagicMock(side_effect=[{"ticker": "X"},
                                                 {"market": "US"}]),
                execute_write=MagicMock(return_value=1)):
            result = json.loads(positions.add_position("AAPL", 10.0, 1,
                                                       market="CN"))
        assert result.get("ok") is True
        assert result.get("replaced_market") == "US"


class TestRemovePosition:
    def test_removes(self):
        with patch("db.pg.fetch_write",
                   MagicMock(return_value=[{"ticker": "AAPL"}])) as mock:
            result = json.loads(positions.remove_position("aapl"))
        assert result.get("removed") == "AAPL"
        assert "%s" in mock.call_args.args[0]

    def test_absent_reports_error(self):
        with patch("db.pg.fetch_write", MagicMock(return_value=[])):
            assert "error" in json.loads(positions.remove_position("AAPL"))


# ------------------------------------------------------------ 分币种汇总（这次改造的重点）


class TestSummaryByCurrency:
    """美元和人民币必须分开算——这是这次改造的核心。"""

    @staticmethod
    def _list(rows):
        with patch("db.pg.query", MagicMock(return_value=rows)):
            return json.loads(positions.list_positions())

    def test_two_currencies_are_separate_buckets(self):
        result = self._list([
            _row("AAPL", "USD", close=220.0, avg_cost=200.0, shares=10),
            _row("600519.SH", "CNY", close=1400.0, avg_cost=1300.0, shares=100),
        ])
        summary = result["summary"]
        assert set(summary) == {"USD", "CNY"}
        assert summary["USD"]["count"] == 1
        assert summary["CNY"]["count"] == 1

    def test_totals_are_not_mixed_across_currencies(self):
        result = self._list([
            _row("AAPL", "USD", close=220.0, avg_cost=200.0, shares=10),
            _row("600519.SH", "CNY", close=1400.0, avg_cost=1300.0, shares=100),
        ])
        summary = result["summary"]
        assert summary["USD"]["total_value"] == 2200.0
        assert summary["USD"]["total_cost"] == 2000.0
        assert summary["USD"]["total_pnl"] == 200.0
        assert summary["CNY"]["total_value"] == 140000.0
        assert summary["CNY"]["total_cost"] == 130000.0
        assert summary["CNY"]["total_pnl"] == 10000.0

    def test_no_cross_currency_grand_total(self):
        """汇总里绝不能出现一个把美元人民币加在一起的总数。"""
        result = self._list([
            _row("AAPL", "USD", close=220.0, avg_cost=200.0, shares=10),
            _row("600519.SH", "CNY", close=1400.0, avg_cost=1300.0, shares=100),
        ])
        assert "total_value" not in result["summary"]
        assert "total_pnl" not in result["summary"]
        assert set(result["summary"]) <= {"USD", "CNY"}

    def test_same_currency_rows_are_added_up(self):
        result = self._list([
            _row("AAPL", "USD", close=220.0, avg_cost=200.0, shares=10),
            _row("MSFT", "USD", close=400.0, avg_cost=500.0, shares=2),
        ])
        usd = result["summary"]["USD"]
        assert usd["count"] == 2
        assert usd["total_value"] == 2200.0 + 800.0
        assert usd["total_cost"] == 2000.0 + 1000.0
        assert usd["total_pnl"] == 0.0

    def test_pnl_pct_is_per_currency(self):
        result = self._list([
            _row("AAPL", "USD", close=220.0, avg_cost=200.0, shares=10),
            _row("600519.SH", "CNY", close=1300.0, avg_cost=1300.0, shares=100),
        ])
        assert result["summary"]["USD"]["total_pnl_pct"] == 10.0
        assert result["summary"]["CNY"]["total_pnl_pct"] == 0.0

    def test_row_without_price_still_counts_its_cost(self):
        """没有行情的票（比如 A股还没进数据）不能把整个汇总带崩。"""
        result = self._list([
            _row("600519.SH", "CNY", close=None, avg_cost=1300.0, shares=100),
        ])
        cny = result["summary"]["CNY"]
        assert cny["count"] == 1
        assert cny["total_cost"] == 130000.0
        assert cny["total_value"] == 0.0

    def test_missing_currency_falls_back_to_market(self):
        """currency 列允许为空，兜底要按 market 猜，不能全归到美元。"""
        row = _row("600519.SH", "CNY", close=1400.0, avg_cost=1300.0, shares=100)
        row["currency"] = None
        result = self._list([row])
        assert set(result["summary"]) == {"CNY"}

    def test_zero_cost_gives_none_pct_not_crash(self):
        row = _row("AAPL", "USD", close=10.0, avg_cost=0.0, shares=1)
        result = self._list([row])
        assert result["summary"]["USD"]["total_pnl_pct"] is None

    def test_empty_positions_returns_empty_summary(self):
        result = self._list([])
        assert result["positions"] == []
        assert result["summary"] == {}

    def test_output_is_json_serializable(self):
        result = self._list([
            _row("AAPL", "USD", close=220.0, avg_cost=200.0, shares=10)])
        json.dumps(result)


# ------------------------------------------------------------ 真库读路径


class TestListSqlAgainstRealDatabase:
    """SQL 要真能在 Postgres 上跑通，并且不许退化成全表扫。"""

    def test_list_positions_runs_against_real_db(self):
        result = json.loads(positions.list_positions())
        assert "error" not in result, result.get("error")
        assert "positions" in result and "summary" in result

    def test_sql_is_market_scoped(self):
        """日线表混了两个市场，连表必须带 market，否则 A股会混进美股。"""
        sql = positions.LIST_SQL.lower()
        assert "market" in sql
        assert "?" not in sql

    def test_sql_does_not_full_scan_the_daily_table(self):
        """取最新一天不能写成会扫全表的形式。

        日线表 275 万行。简报里那种
        ``group by market`` 的 latest 公共表达式实测是并行全表扫，
        执行计划代价 17.6 万；按票走主键索引倒扫是 1.8 千。
        """
        plan = "\n".join(r["QUERY PLAN"] for r in
                         pg.query("explain " + positions.LIST_SQL))
        assert "Seq Scan on stock_ohlcv_daily" not in plan, plan
        cost = float(plan.splitlines()[0].split("cost=")[1].split("..")[1]
                     .split(" ")[0])
        assert cost < 50_000, f"查询计划代价 {cost}，太贵了：\n{plan}"


@pytest.fixture
def probe_guard():
    """保证探针数据一定被清掉，并且真实持仓一行都没动。"""
    before = pg.query_one("select count(*) as n from user_positions")["n"]
    try:
        yield
    finally:
        pg.execute_write("delete from user_positions where ticker = %s", (PROBE,))
        after = pg.query_one("select count(*) as n from user_positions")["n"]
        assert after == before, f"没清干净：跑之前 {before} 行，跑之后 {after} 行"
        assert pg.query_one(
            "select 1 as x from user_positions where ticker = %s", (PROBE,)) is None


class TestRealRoundTrip:
    """真库往返一次：写一行探针，读回来，再删掉。用完必清。"""

    def test_probe_position_can_be_listed_and_removed(self, probe_guard):
        pg.execute_write(
            "insert into user_positions (ticker, market, avg_cost, shares, currency) "
            "values (%s, %s, %s, %s, %s)", (PROBE, "CN", 100.0, 3, "CNY"))

        result = json.loads(positions.list_positions())
        row = next(r for r in result["positions"] if r["ticker"] == PROBE)
        assert row["currency"] == "CNY"
        assert row["market"] == "CN"
        assert result["summary"]["CNY"]["count"] >= 1
        assert result["summary"]["CNY"]["total_cost"] >= 300.0

        removed = json.loads(positions.remove_position(PROBE))
        assert removed.get("removed") == PROBE
        assert pg.query_one(
            "select 1 as x from user_positions where ticker = %s", (PROBE,)) is None

    def test_remove_absent_probe_reports_error(self, probe_guard):
        assert "error" in json.loads(positions.remove_position(PROBE))


# ------------------------------------------------------------ 等上游补数据


class TestCnDataNotReadyYet:
    """A股这条路真数据验证不了——``stock_ohlcv_daily`` 现在只有 US 一个市场。

    这不是代码问题，是上游 tt-data-hub 的 cn_daily 任务还没跑。
    分币种的逻辑已经用假数据测透了（见 TestSummaryByCurrency）；
    2026-08-29 上游 cn_daily / meta_refresh 已跑，A股 1500 只票、105 万行日线
    都在库里了，xfail 标记已摘掉。从现在起这两条是真正的守卫。
    """

    def test_cn_tickers_exist_in_meta(self):
        n = pg.query_one("select count(*) as n from stocks_meta "
                         "where market = %s", ("CN",))["n"]
        assert n > 0, "A股元数据还没进来，add_position 的存在性校验过不了"

    def test_cn_daily_bars_exist_for_pricing(self):
        n = pg.query_one("select count(*) as n from stock_ohlcv_daily "
                         "where market = %s", ("CN",))["n"]
        assert n > 0, "A股日线还没进来，CN 持仓查不到现价"
