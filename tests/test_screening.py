# -*- coding: utf-8 -*-
"""选股工具 screen_stocks 的测试。

跑的是真库（只读账号），不用 mock —— 这个工具的价值全在 SQL 上，
mock 掉数据库等于什么都没测。
"""
import datetime
import json
import time

import pytest

from db import pg
from tools import screening


CN_SUFFIXES = (".SH", ".SZ", ".BJ")


def screen(**kwargs) -> dict:
    """跑一次筛选并解析结果。"""
    return json.loads(screening.screen_stocks(**kwargs))


@pytest.fixture(scope="module")
def us_latest_date():
    row = pg.query_one(
        "select max(date) as d from stock_ohlcv_daily where market = %s", ("US",))
    return row["d"]


# --------------------------------------------------------------------------
# 必须给筛选条件
# --------------------------------------------------------------------------
class TestFilterRequirement:
    def test_no_filter_is_rejected(self):
        """一个条件都不给会返回全市场，上下文直接爆掉。"""
        assert "error" in screen()

    def test_screen_type_and_limit_alone_are_not_filters(self):
        assert "error" in screen(screen_type="swing", limit=5)

    def test_single_filter_is_enough(self):
        result = screen(min_rsi=50)
        assert "error" not in result, result
        assert result["count"] > 0


# --------------------------------------------------------------------------
# 市场隔离：日线表混了美股和 A股，漏了 market 条件会串
# --------------------------------------------------------------------------
class TestMarketIsolation:
    def test_us_results_contain_no_cn_tickers(self):
        result = screen(market="US", min_rsi=30, limit=30)
        assert result["candidates"]
        for row in result["candidates"]:
            assert not row["ticker"].endswith(CN_SUFFIXES), row["ticker"]

    def test_default_market_is_us(self):
        assert screen(min_rsi=30)["filters"]["market"] == "US"

    def test_cn_results_are_all_cn_tickers(self):
        result = screen(market="CN", min_rsi=30, limit=30)
        assert "error" not in result, result
        if not result["candidates"]:
            pytest.skip("A股日线还没有数据，等 tt-data-hub 的 A股采集任务跑过")
        for row in result["candidates"]:
            assert row["ticker"].endswith(CN_SUFFIXES), row["ticker"]

    def test_market_is_case_insensitive(self):
        assert "error" not in screen(market="us", min_rsi=50)

    def test_invalid_market_rejected(self):
        assert "error" in screen(market="XX", min_rsi=50)

    def test_daily_table_is_filtered_by_market(self):
        """日线表眼下只有美股，市场串没串用真数据证不出来，
        只能直接盯 SQL：日线表那一侧必须带 market 条件。
        等 A股数据到位，上面 test_cn_results_are_all_cn_tickers 会接管。"""
        sql, params = screening.build_screen_sql(
            as_of=datetime.date(2026, 8, 28), market="CN", min_rsi=30, limit=20)
        assert params["market"] == "CN"
        # 主 where 子句缩进 8 格，基本面公共表达式里那个缩进 12 格，靠这个区分
        main_where = sql.rsplit("\n        where ", 1)[1]
        assert "o.market = %(market)s" in main_where, main_where

    def test_latest_date_is_queried_per_market(self):
        """最新交易日按市场各查各的，两个市场休市日不一样。"""
        assert "market = %(market)s" in screening.LATEST_DAY_SQL
        assert screening.latest_trading_day("US") is not None

    def test_market_without_data_short_circuits(self):
        """某个市场一行数据都没有时直接说清楚，不发主查询，也不报错。"""
        result = screen(market="CN", min_rsi=30)
        if result["as_of"] is not None:
            pytest.skip("A股已经有数据了，这条短路分支不再适用")
        assert "error" not in result
        assert result["count"] == 0
        assert "note" in result


# --------------------------------------------------------------------------
# 各个筛选条件是不是真的生效
# --------------------------------------------------------------------------
class TestFilters:
    def test_min_rsi_respected(self):
        result = screen(min_rsi=60, limit=30)
        assert result["candidates"]
        for row in result["candidates"]:
            assert row["rsi_14"] >= 60, row

    def test_max_rsi_respected(self):
        result = screen(max_rsi=40, limit=30)
        assert result["candidates"]
        for row in result["candidates"]:
            assert row["rsi_14"] <= 40, row

    def test_rsi_range_respected(self):
        result = screen(min_rsi=45, max_rsi=55, limit=30)
        assert result["candidates"]
        for row in result["candidates"]:
            assert 45 <= row["rsi_14"] <= 55, row

    def test_above_ma50_respected(self):
        result = screen(above_ma=50, limit=30)
        assert result["candidates"]
        for row in result["candidates"]:
            assert row["close"] > row["ma_50"], row

    def test_above_ma200_respected(self):
        result = screen(above_ma=200, limit=30)
        assert result["candidates"]
        for row in result["candidates"]:
            assert row["close"] > row["ma_200"], row

    def test_invalid_above_ma_rejected(self):
        assert "error" in screen(above_ma=99)

    def test_min_market_cap_respected(self):
        """参数单位是十亿美元，库里存的是绝对值。"""
        result = screen(min_market_cap=500, limit=30)
        assert result["candidates"]
        for row in result["candidates"]:
            assert row["market_cap"] >= 500e9, row

    def test_max_pe_respected(self):
        """原逻辑允许市盈率为空的票通过（很多票没有市盈率）。"""
        result = screen(max_pe=15, limit=30)
        assert result["candidates"]
        for row in result["candidates"]:
            assert row["pe_ratio"] is None or row["pe_ratio"] <= 15, row

    def test_min_growth_respected(self):
        """入参是百分比（20 就是 20%），返回值也是百分点，所以直接比 20。

        中间那一段「除以 100 再跟库里的小数列比」是实现细节：
        库里 `revenue_growth_yoy` 存 0.20 表示增长 20%，进 SQL 时要除，
        出结果时要乘。入参和返回值在同一个量纲上，这条断言才读得通。
        """
        result = screen(min_growth=20, limit=30)
        assert result["candidates"]
        for row in result["candidates"]:
            assert row["revenue_growth_yoy"] >= 20, row

    def test_min_margin_respected(self):
        result = screen(min_margin=50, limit=30)
        assert result["candidates"]
        for row in result["candidates"]:
            assert row["gross_margin"] >= 50, row

    def test_filters_combine_with_and(self):
        result = screen(min_market_cap=100, min_rsi=50, above_ma=200, limit=30)
        assert "error" not in result, result
        for row in result["candidates"]:
            assert row["market_cap"] >= 100e9
            assert row["rsi_14"] >= 50
            assert row["close"] > row["ma_200"]


# --------------------------------------------------------------------------
# 板块筛选
# --------------------------------------------------------------------------
class TestSector:
    def test_sector_filter_returns_only_that_sector(self):
        result = screen(sector="Technology", min_rsi=20, limit=30)
        assert "error" not in result, result
        assert result["candidates"], "科技股一只都没筛出来，板块筛选是死的"
        for row in result["candidates"]:
            assert (row["sector"] or "").lower().replace(" ", "") == "technology", row

    def test_sector_alias_healthcare(self):
        """兜底表里写的是 'Health Care'，用户会写 'Healthcare'，两种都得认。"""
        a = screen(sector="Healthcare", min_rsi=20, limit=30)["count"]
        b = screen(sector="Health Care", min_rsi=20, limit=30)["count"]
        assert a == b and a > 0

    def test_unknown_sector_returns_empty_not_error(self):
        result = screen(sector="不存在的板块", min_rsi=20)
        assert "error" not in result, result
        assert result["count"] == 0

    def test_sector_source_is_reported(self):
        """行业分类现在来自临时兜底表，结果里必须说清楚，不能装作没这回事。"""
        result = screen(sector="Technology", min_rsi=20)
        assert "sector_source" in result

    def test_stocks_meta_sector_is_populated(self):
        """2026-08-29 上游 meta_refresh 已跑完，这条从 xfail 转正。

        补的过程有波折：第一轮撞上 UW 那个转售端点整体故障（全域 530 约 20 分钟），
        2133 只里 530 只没采到，只补到 1555；把并发从 6 降到 3 重跑之后失败数
        降到 7，最终 2020 只。
        """
        row = pg.query_one("select count(sector) as c from stocks_meta")
        assert row["c"] >= 2000

    def test_fallback_still_earns_its_keep(self):
        """ticker_over20b 那段临时兜底【暂时不删】。

        stocks_meta 补到 2020 只之后，选股池里仍有约 100 只票没有行业分类
        （2018/2121 = 95%），兜底表还能补上其中大部分。等覆盖率接近 100%
        再删。这条测下来要是发现兜底一只都补不到了，就说明可以删了。
        """
        gap = pg.query_one("""
            select count(*) as n from ticker_over20b t
            join stocks_meta m on m.ticker = t.ticker
            where m.delisted_at is null and m.sector is null""")["n"]
        assert gap >= 0  # 只是把这个数字暴露出来，不设门槛
        if gap == 0:
            import warnings
            warnings.warn("选股池行业分类已 100% 覆盖，可以删掉 ticker_over20b 兜底了")


# --------------------------------------------------------------------------
# 基本面表一只票有很多行，连表必须去重
# --------------------------------------------------------------------------
class TestFundamentalsDeduplication:
    def test_fundamentals_really_has_many_rows_per_ticker(self):
        """先证明这个坑是真的存在，否则下面那条测试等于没测。"""
        row = pg.query_one(
            "select count(*) as total, count(distinct ticker) as uniq "
            "from stock_fundamentals")
        assert row["total"] > row["uniq"] * 2

    def test_no_duplicate_tickers_in_result(self):
        result = screen(min_market_cap=10, limit=30)
        tickers = [r["ticker"] for r in result["candidates"]]
        assert len(tickers) == len(set(tickers)), tickers


# --------------------------------------------------------------------------
# 只取最新交易日
# --------------------------------------------------------------------------
class TestLatestDateOnly:
    def test_as_of_is_the_latest_trading_day(self, us_latest_date):
        result = screen(min_rsi=30)
        assert result["as_of"] == us_latest_date.isoformat()

    def test_prices_match_the_latest_day(self, us_latest_date):
        result = screen(min_market_cap=500, limit=5)
        assert result["candidates"]
        for row in result["candidates"]:
            expected = pg.query_one(
                "select close from stock_ohlcv_daily "
                "where ticker = %s and date = %s and market = %s",
                (row["ticker"], us_latest_date, "US"))
            assert expected is not None, row["ticker"]
            assert abs(expected["close"] - row["close"]) < 1e-6, row


# --------------------------------------------------------------------------
# 输出必须是轻量摘要
# --------------------------------------------------------------------------
class TestLightweightOutput:
    def test_limit_capped_at_30(self):
        assert len(screen(min_rsi=10, limit=999)["candidates"]) <= 30

    def test_limit_respected_below_cap(self):
        assert len(screen(min_rsi=10, limit=5)["candidates"]) <= 5

    def test_result_is_json_serialisable_and_small(self):
        """Decimal 和 date 都不能直接进 JSON；访问层已经把 numeric 转成了
        float，这里再确认整包大小压得住。"""
        raw = screening.screen_stocks(min_rsi=10, limit=30)
        assert len(raw) < 60000, f"{len(raw)} 字符，太大了"

    def test_filters_echoed_back(self):
        result = screen(min_rsi=55, max_pe=30)
        assert result["filters"]["min_rsi"] == 55
        assert result["filters"]["max_pe"] == 30
        assert "above_ma" not in result["filters"]


# --------------------------------------------------------------------------
# 伽马墙（期权做市商的对冲压力位，做波段时的天然止盈止损位）
# --------------------------------------------------------------------------
class TestGammaWalls:
    def test_mega_caps_carry_gamma_levels(self):
        result = screen(min_market_cap=1000, limit=30)
        assert result["candidates"]
        with_gex = [r for r in result["candidates"]
                    if r.get("gex_call_wall") is not None]
        if not with_gex:
            pytest.skip("uw_gex_daily 最新一天没数据，等 tt-data-hub 的伽马墙采集")
        for row in with_gex:
            assert isinstance(row["gex_call_wall"], (int, float))

    def test_gex_keys_omitted_when_empty(self):
        """绝大多数票没有伽马墙数据，四个空字段乘 30 行纯属浪费 token。"""
        result = screen(max_pe=15, limit=30)
        for row in result["candidates"]:
            assert "gex_call_wall" not in row or row["gex_call_wall"] is not None


# --------------------------------------------------------------------------
# 性能：相关子查询那个坑不能再回来
# --------------------------------------------------------------------------
class TestPerformance:
    def test_query_finishes_quickly(self):
        start = time.monotonic()
        screening.screen_stocks(min_rsi=50, limit=20)
        elapsed = time.monotonic() - start
        assert elapsed < 3.0, f"耗时 {elapsed:.1f} 秒，说明还在全表扫"

    def test_latest_day_probe_hits_the_index(self):
        """两段式的第一段：单查最新交易日必须走 (market, date desc) 索引。"""
        plan = "\n".join(row["QUERY PLAN"] for row in pg.query(
            "explain " + screening.LATEST_DAY_SQL, {"market": "US"}))
        assert "Seq Scan" not in plan, plan
        assert "idx_ohlcv_daily_market_date" in plan, plan

    def test_main_query_filters_on_the_given_day(self):
        """日期条件掉了就会把 275 万行全部历史都捞回来。"""
        sql, params = screening.build_screen_sql(
            as_of=datetime.date(2026, 8, 28), market="US", min_rsi=50, limit=20)
        assert params["as_of"] == datetime.date(2026, 8, 28)
        assert "o.date = %(as_of)s" in sql.rsplit("\n        where ", 1)[1]

    def test_no_plan_node_scans_the_whole_daily_table(self):
        """盯的是执行计划里**最大的那个节点**，不是顶层代价。

        顶层有 limit 兜着，光看顶层会漏掉底下的全表扫。实测过的三种坏写法
        这条都拦得住：相关子查询 738 万、group by market 公共表达式 18 万、
        逐票 LATERAL 倒扫 13 万。
        """
        sql, params = screening.build_screen_sql(
            as_of=screening.latest_trading_day("US"),
            market="US", min_rsi=50, limit=20)
        lines = [row["QUERY PLAN"] for row in pg.query("explain " + sql, params)]
        plan = "\n".join(lines)
        assert "Seq Scan on stock_ohlcv_daily" not in plan, plan
        worst = max(float(line.split("cost=")[1].split("..")[1].split(" ")[0])
                    for line in lines if "cost=" in line)
        assert worst < 100000, f"计划内最大节点代价 {worst:,.0f}，太高了\n{plan}"
