"""tools/hourly.py 的测试 —— 小时线买点工具。

这个工具存在的理由：做波段的人用日线定方向、用小时线找具体的进场和出场位置。
它回答的是「现在这个价位在最近这段小时线的什么位置、上面压着什么、下面托着什么、
量有没有起来、止损该放多远」。

## 数据现状（2026-08-29 实测，直接决定这个文件能验到什么程度）

`stock_ohlcv_min` 表里 **`timeframe='1h'` 是 0 行**，美股和 A股都没有。
表里现在只有 `1m`（1829 万行）和 `5m`（171 万行），加起来 2000 万行 / 9.3 GB。
所以：

* 纯计算逻辑用**假数据**测（mock 掉 `pg.query`）—— 这是这个文件的主体，
  也是唯一能把逻辑测透的办法。
* **执行计划**（EXPLAIN）用真库测 —— 执行计划跟数据量无关，
  能看出查询有没有踩到那 2000 万行分钟线。
* 需要**真小时线数据**才能跑的，一律标 `xfail`，reason 里写清等哪个上游任务。
  等 `tt-data-hub` 的 `us_hourly` / `cn_hourly` 跑过之后它们会变成 XPASS，
  看到 XPASS 就把 xfail 去掉。

## 一个必须钉死的语义

**`ts` 是这根 K 线【开始】的时刻，不是结束时刻。**
美股来自 Unusual Whales 的 `start_time`，本来就是开始时刻；A股新浪给的是结束时刻，
采集侧已经统一减 60 分钟对齐了。算错这一条，所有点位会整体错一根 K 线。
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from db import pg
from tools import hourly


# ------------------------------------------------------------------ 假数据

# 固定在一个过去的时刻，保证「最后一根有没有走完」的判断稳定
_BASE_TS = datetime(2026, 8, 20, 13, 30, tzinfo=timezone.utc)


def bars_from_closes(closes, *, market="US", highs=None, lows=None,
                     volumes=None, session="RTH", base=_BASE_TS):
    """按收盘价序列造一批小时线，返回顺序跟 SQL 的 `order by ts desc` 一致（倒序）。

    默认 high = close + 1、low = close - 1，方便手算区间。
    """
    n = len(closes)
    highs = highs if highs is not None else [c + 1 for c in closes]
    lows = lows if lows is not None else [c - 1 for c in closes]
    volumes = volumes if volumes is not None else [1_000_000.0] * n

    rows = []
    for i in range(n):
        ts = base + timedelta(hours=i)
        rows.append({
            "ts": ts,
            "ts_local": ts.replace(tzinfo=None),  # SQL 里 `at time zone` 出来是 naive
            "session": session,
            "open": float(closes[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "close": float(closes[i]),
            "volume": float(volumes[i]),
            "market": market,
        })
    return list(reversed(rows))


def run(ticker="AAPL", rows=None, **kwargs):
    """跑一次 get_hourly_setup，把数据库查询换成假数据。"""
    with patch.object(hourly.pg, "query", return_value=rows or []) as q:
        out = json.loads(hourly.get_hourly_setup(ticker, **kwargs))
    return out, q


# ================================================================== SQL 本身


class TestSql:
    """这三个坑漏一个，查询就会把 2000 万行分钟线拖进来，或者把两个市场混在一起。"""

    def test_sql_filters_timeframe_to_hourly(self):
        """小时线跟分钟线挤在同一张表里，靠 timeframe 区分。漏了就是灾难。"""
        assert "timeframe" in hourly.HOURLY_SQL
        assert "'1h'" in hourly.HOURLY_SQL

    def test_sql_filters_market(self):
        """表里混了美股和 A股，不带 market 会把两个市场的票混起来。"""
        assert "market" in hourly.HOURLY_SQL

    def test_sql_uses_postgres_placeholder(self):
        """psycopg2 的占位符是 %s。写成 DuckDB 的 ? 会直接报语法错。"""
        assert "%s" in hourly.HOURLY_SQL
        assert "?" not in hourly.HOURLY_SQL

    def test_sql_has_a_limit(self):
        """必须限制取回的根数，不能把一只票的全部历史拉回来。"""
        assert "limit" in hourly.HOURLY_SQL.lower()

    def test_actual_query_carries_timeframe_and_market(self):
        """不光 SQL 常量里有，实际发出去的那一条也得有。"""
        _, q = run("AAPL", bars_from_closes(list(range(100, 160))))
        sent_sql, sent_params = q.call_args.args
        assert "'1h'" in sent_sql
        assert "market" in sent_sql
        assert "US" in sent_params


# ================================================================== 执行计划


def _plan(fmt="text", ticker="AAPL", market="US"):
    """拿真库的执行计划。表里 1h 是 0 行也没关系 —— 计划跟数据量无关。"""
    params = (hourly.MARKET_TZ[market], ticker, market, 60)
    rows = pg.query(f"explain (verbose, costs, format {fmt}) " + hourly.HOURLY_SQL,
                    params)
    if fmt == "json":
        return rows[0]["QUERY PLAN"][0]["Plan"]
    return "\n".join(r["QUERY PLAN"] for r in rows)


class TestQueryPlan:
    """确认查询是靠索引直接跳到小时线那一段，而不是先扫分钟线再过滤。"""

    def test_the_index_this_query_relies_on_exists(self):
        """守住小时线查询真正依赖的那个索引。

        这条原来守的是 idx_ohlcv_min_hourly —— 那是个部分索引
        btree (ticker, ts DESC) where timeframe='1h'。写这个任务的时候就已经
        实测发现【优化器根本不选它】：它缺 market 列，用它反而要多一道 heap
        上的 market 过滤，代价更高。真正被选中的一直是下面这个。

        2026-08-29 那个用不上的部分索引已经从库里删掉了（连同另外两个冗余
        索引，一共回收约 1.5 GB），所以这条改成守真正在用的那个。
        """
        rows = pg.query(
            "select indexdef from pg_indexes where schemaname='public' "
            "and tablename='stock_ohlcv_min' and indexname=%s",
            ("stock_ohlcv_min_ticker_tf_ts_idx",))
        assert rows, ("索引 stock_ohlcv_min_ticker_tf_ts_idx 不见了 —— "
                      "小时线查询会退化成扫 2000 万行分钟线")
        d = rows[0]["indexdef"]
        for col in ("market", "ticker", "timeframe", "ts"):
            assert col in d, "索引定义里缺 %s 列：%s" % (col, d)

    def test_no_sequential_scan(self):
        """全表顺序扫 2000 万行 / 9.3 GB，绝不允许。"""
        assert "Seq Scan" not in _plan()

    def test_timeframe_is_an_index_condition(self):
        """timeframe 必须落在 Index Cond 里，不能只是 heap 上的 Filter。

        这是「没有扫到分钟线」的直接证据：定位条件里就带了 timeframe='1h'，
        B 树直接跳到那一段，分钟线的行根本不会被读到。
        """
        plan = _plan()
        conds = [ln for ln in plan.splitlines() if "Index Cond" in ln]
        assert conds, plan
        assert any("timeframe" in ln for ln in conds), plan

    def test_market_is_an_index_condition(self):
        plan = _plan()
        conds = [ln for ln in plan.splitlines() if "Index Cond" in ln]
        assert any("market" in ln for ln in conds), plan

    def test_plan_is_cheap(self):
        """带齐三个条件之后，取 60 根的代价应该是个位数。"""
        assert _plan("json")["Total Cost"] < 100

    def test_dropping_timeframe_is_catastrophic(self):
        """反面对照：同样的查询去掉 timeframe，代价立刻涨几个数量级。

        这条不是测实现，是把「为什么每条查询都必须带 timeframe」钉在测试里。
        """
        bad = hourly.HOURLY_SQL.replace("and timeframe = '1h'", "")
        rows = pg.query("explain (costs, format json) " + bad,
                        (hourly.MARKET_TZ["US"], "AAPL", "US", 60))
        bad_cost = rows[0]["QUERY PLAN"][0]["Plan"]["Total Cost"]
        assert bad_cost > _plan("json")["Total Cost"] * 100, bad_cost


# ================================================================== 返回形状


class TestShape:
    def test_returns_json_string(self):
        out, _ = run("AAPL", bars_from_closes(list(range(100, 160))))
        assert isinstance(out, dict)
        assert "error" not in out, out

    def test_contains_key_levels(self):
        """简报约定的字段，一个都不能少。"""
        out, _ = run("AAPL", bars_from_closes(list(range(100, 160))))
        assert {"ticker", "market", "last_close", "recent_high", "recent_low",
                "ma_20h", "ma_50h", "position_pct", "bars_used"} <= set(out)

    def test_contains_swing_decision_fields(self):
        """一个做波段的人问「现在能不能进」，光有均线不够 ——
        还得知道量能起没起、止损放多远、上下最近的关键位在哪。"""
        out, _ = run("AAPL", bars_from_closes(list(range(100, 160))))
        assert {"vol_ratio", "atr_14h", "atr_pct", "rsi_14h",
                "support", "resistance", "signals", "summary"} <= set(out)

    def test_result_is_lightweight_no_raw_bars(self):
        """核心设计规则：只交结论，不交原始 K 线。

        200 根 K 线丢给 Claude 是纯粹的 token 浪费，计算必须在这边做完。
        """
        out, _ = run("AAPL", bars_from_closes(list(range(100, 300))), bars=200)
        for key, value in out.items():
            assert not (isinstance(value, list) and value
                        and isinstance(value[0], dict)), \
                f"{key} 是原始行数组，不该交给 Claude"
        assert len(json.dumps(out, ensure_ascii=False)) < 3000

    def test_json_is_serializable_with_no_decimals(self):
        """numeric 已经在 db/pg.py 转成 float 了，这里再确认一遍能序列化。"""
        out, _ = run("AAPL", bars_from_closes(list(range(100, 160))))
        json.dumps(out)


# ================================================================== 价格位置


class TestPriceLevels:
    def test_last_close_is_the_newest_bar(self):
        """SQL 给回来的是倒序，最新的一根在第一个。取错就整个反了。"""
        out, _ = run("AAPL", bars_from_closes([100.0] * 59 + [123.0]))
        assert out["last_close"] == 123.0

    def test_recent_high_and_low_span_the_window(self):
        closes = [100.0] * 60
        highs = [101.0] * 60
        lows = [99.0] * 60
        highs[10] = 150.0
        lows[20] = 50.0
        out, _ = run("AAPL", bars_from_closes(closes, highs=highs, lows=lows))
        assert out["recent_high"] == 150.0
        assert out["recent_low"] == 50.0

    def test_recent_high_not_below_recent_low(self):
        out, _ = run("AAPL", bars_from_closes(list(range(100, 160))))
        assert out["recent_high"] >= out["recent_low"]

    def test_position_pct_between_zero_and_hundred(self):
        out, _ = run("AAPL", bars_from_closes(list(range(100, 160))))
        assert 0 <= out["position_pct"] <= 100

    def test_position_pct_at_the_top(self):
        """收在区间最高点 = 100。"""
        closes = [100.0] * 59 + [110.0]
        highs = [101.0] * 59 + [110.0]
        lows = [99.0] * 60
        out, _ = run("AAPL", bars_from_closes(closes, highs=highs, lows=lows))
        assert out["position_pct"] == 100.0

    def test_position_pct_in_the_middle(self):
        """区间 90~110，收在 100 → 正中间。"""
        closes = [100.0] * 60
        highs = [100.0] * 59 + [110.0]
        lows = [100.0] * 59 + [90.0]
        highs[0] = 110.0
        lows[0] = 90.0
        out, _ = run("AAPL", bars_from_closes(closes, highs=highs, lows=lows))
        assert out["position_pct"] == 50.0

    def test_flat_range_does_not_divide_by_zero(self):
        """一根不动的横盘（high == low）不能把工具炸掉。"""
        out, _ = run("AAPL", bars_from_closes(
            [100.0] * 60, highs=[100.0] * 60, lows=[100.0] * 60))
        assert "error" not in out, out
        assert out["position_pct"] == 50.0

    def test_distance_to_high_and_low(self):
        """离上沿/下沿还有多少个百分点 —— 这是做波段的人真正会看的数。"""
        closes = [100.0] * 60
        highs = [110.0] + [100.0] * 59
        lows = [90.0] + [100.0] * 59
        out, _ = run("AAPL", bars_from_closes(closes, highs=highs, lows=lows))
        assert out["dist_to_high_pct"] == 10.0
        assert out["dist_to_low_pct"] == 10.0

    def test_extremes_carry_their_timestamps(self):
        """光知道高点在 150 没用，还得知道它是什么时候打出来的。"""
        out, _ = run("AAPL", bars_from_closes(list(range(100, 160))))
        assert out["recent_high_at"]
        assert out["recent_low_at"]


# ================================================================== 均线


class TestMovingAverages:
    def test_ma_20h_is_the_mean_of_the_last_20_closes(self):
        closes = [float(i) for i in range(1, 61)]  # 1..60
        out, _ = run("AAPL", bars_from_closes(closes))
        assert out["ma_20h"] == pytest.approx(sum(range(41, 61)) / 20)

    def test_ma_50h_is_the_mean_of_the_last_50_closes(self):
        closes = [float(i) for i in range(1, 61)]
        out, _ = run("AAPL", bars_from_closes(closes))
        assert out["ma_50h"] == pytest.approx(sum(range(11, 61)) / 50)

    def test_ma_is_none_when_not_enough_bars(self):
        """只有 25 根就算不出 50 小时均线，要给 None，不能拿 25 根凑。"""
        out, _ = run("AAPL", bars_from_closes([100.0] * 25), bars=25)
        assert out["ma_20h"] is not None
        assert out["ma_50h"] is None
        assert out["above_ma_50h"] is None

    def test_above_ma_flags(self):
        closes = [100.0] * 59 + [200.0]
        out, _ = run("AAPL", bars_from_closes(closes))
        assert out["above_ma_20h"] is True
        assert out["above_ma_50h"] is True

    def test_below_ma_flags(self):
        closes = [100.0] * 59 + [50.0]
        out, _ = run("AAPL", bars_from_closes(closes))
        assert out["above_ma_20h"] is False

    def test_ma_slope_is_positive_when_rising(self):
        """均线往上还是往下，比站不站上更能说明方向。"""
        out, _ = run("AAPL", bars_from_closes([float(i) for i in range(1, 61)]))
        assert out["ma_20h_slope_pct"] > 0

    def test_ma_slope_is_negative_when_falling(self):
        out, _ = run("AAPL", bars_from_closes([float(i) for i in range(60, 0, -1)]))
        assert out["ma_20h_slope_pct"] < 0


# ================================================================== 量能


class TestVolume:
    def test_vol_ratio_compares_last_bar_to_the_average(self):
        """最后一根放了 3 倍量。"""
        vols = [1_000_000.0] * 59 + [3_000_000.0]
        out, _ = run("AAPL", bars_from_closes([100.0] * 60, volumes=vols))
        assert out["vol_ratio"] == pytest.approx(3.0)

    def test_vol_ratio_excludes_the_last_bar_from_the_average(self):
        """均量里不能把当前这根算进去，否则放量会被自己稀释。"""
        vols = [1_000_000.0] * 19 + [21_000_000.0]
        out, _ = run("AAPL", bars_from_closes([100.0] * 20, volumes=vols), bars=20)
        assert out["vol_ratio"] == pytest.approx(21.0)

    def test_zero_average_volume_does_not_crash(self):
        out, _ = run("AAPL", bars_from_closes([100.0] * 60, volumes=[0.0] * 60))
        assert "error" not in out, out
        assert out["vol_ratio"] is None

    def test_incomplete_last_bar_is_flagged(self):
        """盘中最后一根还没走完，量天然偏小 —— 不标出来就会误判成缩量。"""
        now = datetime.now(timezone.utc)
        rows = bars_from_closes([100.0] * 60,
                                base=now - timedelta(minutes=30 + 59 * 60))
        out, _ = run("AAPL", rows)
        assert out["last_bar_complete"] is False

    def test_complete_last_bar_is_flagged(self):
        out, _ = run("AAPL", bars_from_closes([100.0] * 60))
        assert out["last_bar_complete"] is True


# ================================================================== RSI / ATR


class TestRsiAndAtr:
    def test_rsi_is_100_when_price_only_goes_up(self):
        out, _ = run("AAPL", bars_from_closes([float(i) for i in range(1, 61)]))
        assert out["rsi_14h"] == pytest.approx(100.0)

    def test_rsi_is_0_when_price_only_goes_down(self):
        out, _ = run("AAPL", bars_from_closes([float(i) for i in range(60, 0, -1)]))
        assert out["rsi_14h"] == pytest.approx(0.0)

    def test_rsi_is_in_range(self):
        closes = [100 + (i % 7) - 3 for i in range(60)]
        out, _ = run("AAPL", bars_from_closes(closes))
        assert 0 <= out["rsi_14h"] <= 100

    def test_rsi_is_none_when_not_enough_bars(self):
        out, _ = run("AAPL", bars_from_closes([100.0] * 20), bars=20)
        assert out["rsi_14h"] is not None  # 20 根够算 14 周期
        out, _ = run("AAPL", bars_from_closes([100.0] * 10), bars=10)
        assert out["rsi_14h"] is None

    def test_atr_measures_the_bar_range(self):
        """每根都是固定 4 块钱的振幅，ATR 就该是 4。"""
        closes = [100.0] * 60
        out, _ = run("AAPL", bars_from_closes(
            closes, highs=[102.0] * 60, lows=[98.0] * 60))
        assert out["atr_14h"] == pytest.approx(4.0)

    def test_atr_pct_is_relative_to_price(self):
        """ATR 占现价的百分比 —— 止损放多远直接看这个。"""
        out, _ = run("AAPL", bars_from_closes(
            [100.0] * 60, highs=[102.0] * 60, lows=[98.0] * 60))
        assert out["atr_pct"] == pytest.approx(4.0)


# ================================================================== 支撑阻力


class TestSupportAndResistance:
    def test_resistance_is_the_nearest_swing_high_above_price(self):
        """上方最近的那个摆动高点 —— 冲上去之前会先在那撞一下。"""
        closes = [100.0] * 60
        highs = [101.0] * 60
        highs[20] = 130.0   # 远处的高点
        highs[40] = 115.0   # 更近的高点，才是真正压着的位置
        out, _ = run("AAPL", bars_from_closes(closes, highs=highs))
        assert out["resistance"] == 115.0

    def test_support_is_the_nearest_swing_low_below_price(self):
        closes = [100.0] * 60
        lows = [99.0] * 60
        lows[20] = 70.0
        lows[40] = 85.0
        out, _ = run("AAPL", bars_from_closes(closes, lows=lows))
        assert out["support"] == 85.0

    def test_falls_back_to_range_edges_when_no_swing_found(self):
        """一根摆动点都找不到（完全横盘）时也要给个位置，不能是 None。"""
        out, _ = run("AAPL", bars_from_closes(
            [100.0] * 60, highs=[100.0] * 60, lows=[100.0] * 60))
        assert out["resistance"] is not None
        assert out["support"] is not None

    def test_distance_to_levels_is_reported(self):
        out, _ = run("AAPL", bars_from_closes(list(range(100, 160))))
        assert "dist_to_resistance_pct" in out
        assert "dist_to_support_pct" in out


# ================================================================== 结论文本


class TestSignalsAndSummary:
    def test_signals_mention_being_above_the_moving_average(self):
        out, _ = run("AAPL", bars_from_closes([float(i) for i in range(1, 61)]))
        assert any("20" in s for s in out["signals"]), out["signals"]

    def test_signals_mention_a_volume_surge(self):
        vols = [1_000_000.0] * 59 + [5_000_000.0]
        out, _ = run("AAPL", bars_from_closes([100.0] * 60, volumes=vols))
        assert any("量" in s for s in out["signals"]), out["signals"]

    def test_summary_is_one_short_chinese_line(self):
        out, _ = run("AAPL", bars_from_closes(list(range(100, 160))))
        assert isinstance(out["summary"], str)
        assert 0 < len(out["summary"]) < 300


# ================================================================== 参数


class TestBarsArgument:
    def test_respects_bars_argument(self):
        out, q = run("AAPL", bars_from_closes([100.0] * 30), bars=30)
        assert out["bars_used"] <= 30
        assert 30 in q.call_args.args[1]

    def test_bars_is_clamped_at_the_top(self):
        """别让人一次拉一年的小时线回来。"""
        _, q = run("AAPL", bars_from_closes([100.0] * 60), bars=100000)
        assert max(x for x in q.call_args.args[1] if isinstance(x, int)) <= 200

    def test_bars_is_clamped_at_the_bottom(self):
        _, q = run("AAPL", bars_from_closes([100.0] * 60), bars=1)
        assert min(x for x in q.call_args.args[1] if isinstance(x, int)) >= 20

    def test_bars_used_reflects_what_came_back(self):
        out, _ = run("AAPL", bars_from_closes([100.0] * 37), bars=60)
        assert out["bars_used"] == 37


class TestMarketInference:
    """表里混了美股和 A股，每条查询都必须带 market。工具签名里没有市场，
    就只能从代码后缀推 —— 推错了会查出个空。"""

    @pytest.mark.parametrize("ticker", ["600519.SH", "000001.SZ", "430139.BJ"])
    def test_a_share_suffix_means_cn(self, ticker):
        _, q = run(ticker, bars_from_closes([100.0] * 60, market="CN"))
        assert "CN" in q.call_args.args[1]

    @pytest.mark.parametrize("ticker", ["AAPL", "BRK.B", "NVDA"])
    def test_plain_letters_mean_us(self, ticker):
        _, q = run(ticker, bars_from_closes([100.0] * 60))
        assert "US" in q.call_args.args[1]

    def test_explicit_market_wins(self):
        _, q = run("AAPL", bars_from_closes([100.0] * 60, market="CN"),
                   market="cn")
        assert "CN" in q.call_args.args[1]

    def test_ticker_is_normalised(self):
        _, q = run("  aapl  ", bars_from_closes([100.0] * 60))
        assert "AAPL" in q.call_args.args[1]

    def test_bare_six_digit_code_is_rejected_with_a_hint(self):
        """A股裸代码库里查不到，直接告诉用户要带后缀，别让他等一个空结果。"""
        out, _ = run("600519", [])
        assert "error" in out
        assert ".SH" in out["error"]


class TestTimestampSemantics:
    """ts 是这根 K 线【开始】的时刻。算错这条，所有点位整体错一根。"""

    def test_period_says_the_timestamp_is_a_bar_start(self):
        out, _ = run("AAPL", bars_from_closes([100.0] * 60))
        assert "开始" in out["period"]["note"]

    def test_period_reports_the_local_market_timezone(self):
        out, _ = run("AAPL", bars_from_closes([100.0] * 60))
        assert out["period"]["timezone"] == "America/New_York"

    def test_cn_uses_shanghai_time(self):
        out, _ = run("600519.SH", bars_from_closes([100.0] * 60, market="CN"))
        assert out["period"]["timezone"] == "Asia/Shanghai"

    def test_period_runs_from_oldest_to_newest(self):
        out, _ = run("AAPL", bars_from_closes([100.0] * 60))
        assert out["period"]["from"] < out["period"]["to"]

    def test_query_converts_to_local_time_in_sql(self):
        """时区换算放在 Postgres 侧做，不依赖 Windows 上有没有 tzdata。"""
        assert "at time zone" in hourly.HOURLY_SQL.lower()

    def test_sessions_are_reported(self):
        """美股有盘前盘后（ETH）和盘中（RTH）。混进来了要让人看得见。"""
        out, _ = run("AAPL", bars_from_closes([100.0] * 60))
        assert out["sessions"] == ["RTH"]


# ================================================================== 出错路径


class TestErrors:
    def test_unknown_ticker_reports_error(self):
        out, _ = run("ZZZZNOTREAL", [])
        assert "error" in out

    def test_missing_data_error_explains_what_to_do(self):
        out, _ = run("ZZZZNOTREAL", [])
        assert "小时线" in out["error"]

    def test_empty_ticker_reports_error(self):
        out, _ = run("   ", [])
        assert "error" in out

    def test_database_failure_is_caught_not_raised(self):
        """工具是给 MCP 用的，抛异常等于整个对话断掉，必须包成 JSON。"""
        with patch.object(hourly.pg, "query", side_effect=RuntimeError("库挂了")):
            out = json.loads(hourly.get_hourly_setup("AAPL"))
        assert "error" in out
        assert "库挂了" in out["error"]

    def test_error_is_still_valid_json(self):
        out, _ = run("ZZZZNOTREAL", [])
        json.dumps(out)


# ================================================================== 真数据


@pytest.fixture(scope="module")
def sample_hourly_ticker():
    row = pg.query_one(
        "select market, ticker from stock_ohlcv_min where timeframe = '1h' "
        "group by market, ticker having count(*) >= 60 limit 1")
    if not row:
        pytest.skip("库里一根小时线都没有，先跑 tt-data-hub 的 us_hourly / cn_hourly")
    return row


class TestAgainstRealData:
    """这些要真小时线才能跑。2026-08-29 实测 `timeframe='1h'` 是 0 行，
    美股 A股都没有 —— 是数据没到，不是代码有问题。

    等 tt-data-hub 的 us_hourly / cn_hourly 跑过之后这几条会变成 XPASS，
    看到 XPASS 就把 xfail 去掉。
    """

    def test_us_hourly_data_exists(self):
        n = pg.query_one(
            "select count(*) as n from stock_ohlcv_min "
            "where market = 'US' and timeframe = '1h'")["n"]
        assert n > 0, "美股小时线还没进来，先跑 tt-data-hub 的 us_hourly"

    def test_cn_hourly_data_exists(self):
        n = pg.query_one(
            "select count(*) as n from stock_ohlcv_min "
            "where market = 'CN' and timeframe = '1h'")["n"]
        assert n > 0, "A股小时线还没进来，先跑 tt-data-hub 的 cn_hourly"

    def test_real_ticker_returns_a_usable_setup(self, sample_hourly_ticker):
        out = json.loads(hourly.get_hourly_setup(sample_hourly_ticker["ticker"]))
        assert "error" not in out, out
        assert out["bars_used"] >= 20
        assert out["recent_high"] >= out["last_close"] >= out["recent_low"]
        assert 0 <= out["position_pct"] <= 100

    def test_cn_bars_start_at_0930(self):
        """A股一天 4 根，ts 减 60 分钟对齐后应该是 09:30 / 10:30 / 13:00 / 14:00。
        如果出现 15:00，说明采集侧的对齐没生效。"""
        rows = pg.query(
            "select distinct (ts at time zone 'Asia/Shanghai')::time as t "
            "from stock_ohlcv_min where market = 'CN' and timeframe = '1h' "
            "order by t")
        assert rows, "A股小时线还没进来"
        times = {str(r["t"])[:5] for r in rows}
        assert times <= {"09:30", "10:30", "13:00", "14:00"}, times


class TestHourlyGoesLiveFirst:
    """★ 两个市场都是先走实时，拿不到才退回数据库 ★

    **A股（2026-09-01 改的）**：库里的 A股小时线只覆盖活跃池（约 150 只），
    而活跃池的粗筛条件是「站上 50 日均线 + 放量」——那是在挑今天强势的票，
    跟「用户想看哪只票」完全是两回事。当时贵州茅台、五粮液、宁德时代、
    比亚迪库里一根小时线都没有，`analyze_ticker` 那一块直接是空的。
    新浪一次调用给约 1970 根、2~3 秒，而这里最多只要 200 根。

    **美股（2026-09-03 改的）**：`tt-data-hub` 把 `us_hourly` 从每天定时改成了
    按需跑，crontab 里 09:40 那行撤掉了——库里那份美股小时线从此不再自己更新，
    会一天比一天旧。同一天顺带发现覆盖面也是缺的：库里只有活跃池那 247 只票
    有小时线，池子外的票一根都没有，跟 A股当时的毛病一模一样。
    UW 的 1h 接口一次给整个 3M 窗口、约 13 秒。

    退回库里那条路留着当兜底，但**必须让调用方知道拿到的是旧数据**——
    所以还要盯住 `source` 字段和结论里那句提示。
    """

    @staticmethod
    def _live_bars(n=40, start_close=10.0, market="CN"):
        base = datetime(2026, 9, 1, 1, 30, tzinfo=timezone.utc)
        out = []
        for i in range(n):
            ts = base + timedelta(hours=i)
            close = start_close + i * 0.1
            out.append({
                "ts": ts, "ts_local": ts.replace(tzinfo=None),
                "session": "RTH", "market": market,
                "open": close, "high": close + 0.2,
                "low": close - 0.2, "close": close, "volume": 1000.0,
            })
        return out

    def test_cn_uses_live_and_does_not_touch_the_database(self):
        with patch("core.akshare.hourly", return_value=self._live_bars()) as live, \
             patch("tools.hourly.pg.query") as query:
            result = hourly.hourly_setup("600519.SH", bars=30)
        assert "error" not in result, result
        assert live.called, "A股应该先走实时接口"
        assert not query.called, "实时拿到了就不该再查库"

    def test_cn_falls_back_to_the_database_when_live_is_empty(self):
        rows = bars_from_closes([10 + i * 0.1 for i in range(40)], market="CN")
        with patch("core.akshare.hourly", return_value=[]) as live, \
             patch("tools.hourly.pg.query", return_value=rows) as query:
            result = hourly.hourly_setup("600519.SH", bars=30)
        assert "error" not in result, result
        assert live.called and query.called, "实时空了必须退回库里"

    def test_live_failure_does_not_break_the_tool(self):
        """实时那一路抛异常也不能让工具挂掉——照样退回库里。"""
        rows = bars_from_closes([10 + i * 0.1 for i in range(40)], market="CN")
        with patch("core.akshare.hourly", side_effect=RuntimeError("接口挂了")), \
             patch("tools.hourly.pg.query", return_value=rows):
            result = hourly.hourly_setup("600519.SH", bars=30)
        assert "error" not in result, result

    def test_us_uses_uw_live_and_does_not_touch_the_database(self):
        with patch("core.uw.hourly",
                   return_value=self._live_bars(market="US")) as live, \
             patch("tools.hourly.pg.query") as query:
            result = hourly.hourly_setup("AAPL", bars=30)
        assert "error" not in result, result
        assert live.called, "美股应该先走 UW 实时接口"
        assert not query.called, "实时拿到了就不该再查库"

    def test_us_falls_back_to_the_database_when_live_is_empty(self):
        rows = bars_from_closes([10 + i * 0.1 for i in range(40)], market="US")
        with patch("core.uw.hourly", return_value=[]) as live, \
             patch("tools.hourly.pg.query", return_value=rows) as query:
            result = hourly.hourly_setup("AAPL", bars=30)
        assert "error" not in result, result
        assert live.called and query.called, "实时空了必须退回库里"

    def test_us_live_failure_does_not_break_the_tool(self):
        rows = bars_from_closes([10 + i * 0.1 for i in range(40)], market="US")
        with patch("core.uw.hourly", side_effect=RuntimeError("UW 挂了")), \
             patch("tools.hourly.pg.query", return_value=rows):
            result = hourly.hourly_setup("AAPL", bars=30)
        assert "error" not in result, result

    def test_each_market_calls_only_its_own_live_interface(self):
        """两条实时路不许串台：美股走 UW、A股走新浪。"""
        rows = bars_from_closes([10 + i * 0.1 for i in range(40)], market="US")
        with patch("core.akshare.hourly") as sina, \
             patch("core.uw.hourly", return_value=[]), \
             patch("tools.hourly.pg.query", return_value=rows):
            hourly.hourly_setup("AAPL", bars=30)
        assert not sina.called, "美股不该碰 AKShare"

        rows = bars_from_closes([10 + i * 0.1 for i in range(40)], market="CN")
        with patch("core.uw.hourly") as unusual_whales, \
             patch("core.akshare.hourly", return_value=[]), \
             patch("tools.hourly.pg.query", return_value=rows):
            hourly.hourly_setup("600519.SH", bars=30)
        assert not unusual_whales.called, "A股不该碰 UW"

    def test_error_message_no_longer_blames_the_watchlist(self):
        """两边都空时的提示不能再说「加进自选就会开始采」。

        两个市场现在都是实时拉的，加不加自选都一样；
        那句话会把人引到错的地方去（美股那句还多错一层：上游的定时任务
        2026-09-03 已经撤了，加进自选也不会有人去采）。
        """
        with patch("core.akshare.hourly", return_value=[]), \
             patch("core.uw.hourly", return_value=[]), \
             patch("tools.hourly.pg.query", return_value=[]):
            cn = hourly.hourly_setup("600519.SH", bars=30)
            us = hourly.hourly_setup("AAPL", bars=30)
        for result in (cn, us):
            assert "error" in result
            assert "自选" not in result["error"], result["error"]


class TestDataSourceIsReported:
    """★ 拿到的这批 K 线是实时的还是从库里翻出来的，必须说清楚 ★

    上游 2026-09-03 把美股小时线改成按需采集之后，库里那份不再每天更新。
    退回库里拿到的可能是好几天前的行情，而旧行情算出来的支撑阻力、量比
    看着跟实时的一模一样——不标出来，没有任何人分得清。
    """

    @staticmethod
    def _live_bars(market):
        base = datetime(2026, 9, 1, 1, 30, tzinfo=timezone.utc)
        return [{
            "ts": base + timedelta(hours=i),
            "ts_local": (base + timedelta(hours=i)).replace(tzinfo=None),
            "session": "RTH", "market": market,
            "open": 10.0 + i * 0.1, "high": 10.3 + i * 0.1,
            "low": 9.8 + i * 0.1, "close": 10.0 + i * 0.1, "volume": 1000.0,
        } for i in range(40)]

    def test_live_us_is_tagged_uw_live(self):
        with patch("core.uw.hourly", return_value=self._live_bars("US")), \
             patch("tools.hourly.pg.query"):
            result = hourly.hourly_setup("AAPL", bars=30)
        assert result["source"] == "uw_live"

    def test_live_cn_is_tagged_akshare_live(self):
        with patch("core.akshare.hourly", return_value=self._live_bars("CN")), \
             patch("tools.hourly.pg.query"):
            result = hourly.hourly_setup("600519.SH", bars=30)
        assert result["source"] == "akshare_live"

    def test_database_fallback_is_tagged_db(self):
        rows = bars_from_closes([10 + i * 0.1 for i in range(40)], market="US")
        with patch("core.uw.hourly", return_value=[]), \
             patch("tools.hourly.pg.query", return_value=rows):
            result = hourly.hourly_setup("AAPL", bars=30)
        assert result["source"] == "db"

    def test_database_fallback_says_so_in_the_signals(self):
        """光有一个 source 字段不够——结论那几句话也得说出来。

        Claude 读的是 signals 和 summary，一个它不认识的字段值
        很容易就被当成噪音跳过去了。
        """
        rows = bars_from_closes([10 + i * 0.1 for i in range(40)], market="US")
        with patch("core.uw.hourly", return_value=[]), \
             patch("tools.hourly.pg.query", return_value=rows):
            result = hourly.hourly_setup("AAPL", bars=30)
        assert any("库里" in line for line in result["signals"]), result["signals"]

    def test_live_data_does_not_get_the_stale_warning(self):
        """实时拿到的就别再说「可能不是最新行情」，那是噪音。"""
        with patch("core.uw.hourly", return_value=self._live_bars("US")), \
             patch("tools.hourly.pg.query"):
            result = hourly.hourly_setup("AAPL", bars=30)
        assert not any("库里" in line for line in result["signals"])
