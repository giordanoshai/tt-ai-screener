# -*- coding: utf-8 -*-
"""市场环境。美股读现成的宽度表，A股当场算。"""
import datetime

import pytest

from core import regime


def test_us_regime_reads_breadth_table():
    out = regime.get("US")
    assert out["market"] == "US"
    assert isinstance(out["date"], datetime.date)
    assert out["source"] == "market_breadth_daily"
    assert 0 <= out["pct_above_ma20"] <= 100


def test_us_regime_says_it_is_sp500_only():
    """market_breadth_daily 是标普 500 口径，不是全市场，得说清楚。"""
    out = regime.get("US")
    assert "标普" in out["note"] or "500" in out["note"]


def test_cn_regime_counts_only_cn_tickers():
    """市场过滤写错时必须红。

    只断言「涨跌家数 > 100」是拦不住的——美股口径、混合口径也都远超 100，
    而那正是规矩 2 要防的那类事故。所以直接跟库里 A股当日的真实票数比。
    """
    from db import pg

    from core import snapshot as snap_mod

    day = snap_mod.latest_trading_day("CN")
    expected = pg.query_one(
        "select count(*) as n from stock_ohlcv_daily "
        "where market = 'CN' and date = %s", (day,))
    out = regime.get("CN")
    assert out["source"] == "stock_ohlcv_daily"
    assert out["date"] == day
    assert out["total"] == expected["n"]
    assert out["advances"] + out["declines"] <= out["total"]


def test_cn_regime_limit_up_count_matches_the_table():
    """涨停家数要跟涨停表对得上，不能只判键在不在。"""
    from db import pg

    row = pg.query_one(
        "select count(*) as n from cn_limit_pool_daily "
        "where snapshot_date = (select max(snapshot_date) from cn_limit_pool_daily)")
    out = regime.get("CN")
    assert out["limit_up_count"] == row["n"]


def test_regime_bad_market_raises():
    with pytest.raises(ValueError):
        regime.get("HK")


def test_freshness_days_since_matches_the_calendar():
    """陈旧判断要真的算过，不能只判它返回了个 bool。"""
    import datetime

    from core import snapshot as snap_mod

    out = regime.freshness("US")
    day = snap_mod.latest_trading_day("US")
    assert out["market"] == "US"
    assert out["latest_trading_day"] == day
    assert out["days_since"] == (datetime.date.today() - day).days
    assert out["stale"] == (out["days_since"] > snap_mod.STALE_DAYS)
    assert isinstance(out["failed_jobs"], list)
    assert isinstance(out["partial_jobs"], list)


def test_freshness_separates_failed_from_partial():
    """partial 是「跑完了但某个可选源没取到」，不是失败。混在一起报警，
    用户会学会无视这个警报，然后真出事那次也一起无视。"""
    out = regime.freshness("US")
    assert not (set(out["failed_jobs"]) & set(out["partial_jobs"]))
    for name in out["failed_jobs"]:
        assert "(failed)" in name
    for name in out["partial_jobs"]:
        assert "(partial)" in name


def test_freshness_only_reports_jobs_from_that_market():
    """按市场过滤要真的生效。不过滤的话，A股任务会顶着「US」的标签报给用户。"""
    us = regime.freshness("US")
    cn = regime.freshness("CN")
    assert not any(name.startswith("cn_") for name
                   in us["failed_jobs"] + us["partial_jobs"])
    assert not any(name.startswith("us_") for name
                   in cn["failed_jobs"] + cn["partial_jobs"])


# ---------------- 评审 C3：A股「创 20 个交易日新高」的窗口不能含当天 ----------------

def test_cn_new_highs_20_window_excludes_today():
    """跟 `tools/watch_positions.py` 的「20 交易日新低」是同一个 bug：
    库里的 `high_20` 这一列**包含当天**（`core/snapshot.py` 为此专门准备了
    `prior_high_20`），所以早期写法 `close >= high_20` 只在「收盘价正好等于
    当日最高价、而且这个最高价恰好是 20 日最高」时才成立。

    2026-08-28 实测（1500 只 A股）：那种写法命中 155 只，按真实语义
    （当日最高价创出**此前** 20 个交易日以来的新高）应该命中 316 只。

    这里独立按两种口径各算一遍，跟 `regime.get("CN")` 的返回值核对：必须
    等于「不含当天」那个数，而且必须跟「含当天」那个数不一样——两个数如果
    碰巧相等，这条测试就区分不了，会明说测不出来而不是假装通过。
    """
    from core import regime, snapshot
    from db import pg

    day = snapshot.latest_trading_day("CN")
    if day is None:
        pytest.skip("库里没有 A股行情")

    # 两个条件都不能少，少一个这条测试就会拿错的期望值去卡正确的代码：
    #
    #   * `where date = 当天` —— 不能用「每只票最新的一行」（row_number）。
    #     停牌的票最新一行是停牌前那天，拿它当「今天创新高」算进来就多了。
    #     2026-09-01 真踩到：002870.SZ 停牌、没有当天数据，它 8/31 的行被
    #     算了进去，期望值 246 卡着正确的 245 报错。
    #
    #   * `prior_bars = 20` —— 窗口帧不足时 `max() over` 照样给值（只有 3 根
    #     就拿 3 根的最高价），刚上市的票会被算成创 20 日新高。
    #     `core/regime.py` 有这个条件，这里也必须有，否则两边算的不是一件事。
    counts = pg.query_one("""
        with recent as (
          select ticker, date, high, close, high_20,
                 max(high) over (partition by ticker order by date
                                 rows between 20 preceding and 1 preceding)
                     as prior_high,
                 count(high) over (partition by ticker order by date
                                   rows between 20 preceding and 1 preceding)
                     as prior_bars
          from stock_ohlcv_daily
          where market = 'CN' and date > %s::date - 60 and date <= %s
        )
        select
          count(*) filter (where high_20 is not null and close >= high_20)
              as including_today,
          count(*) filter (where prior_high is not null
                             and prior_bars = 20
                             and high >= prior_high)
              as excluding_today
        from recent where date = %s
    """, (day, day, day))

    including, excluding = counts["including_today"], counts["excluding_today"]
    if including == excluding:
        pytest.skip(f"今天两种口径碰巧都是 {including}，区分不了")

    got = regime.get("CN")["new_highs_20"]
    assert got == excluding, (
        f"new_highs_20 应该是「当日最高价创出此前 20 个交易日新高」的只数 "
        f"{excluding}，实际返回 {got}")
    assert got != including, (
        f"返回值等于「把当天算进 high_20」那种口径的 {including}，"
        "说明窗口又把当天算进去了")


def test_cn_new_highs_needs_a_full_window():
    """A股「创 20 日新高」也要窗口够 20 根才算数。

    跟 `tools/watch_positions.py` 的新低是同一个问题：窗口帧不足时
    `max() over (rows between 20 preceding and 1 preceding)` 照样给值
    （只有 3 根就拿 3 根的最高价），刚上市的票会被算成创新高。

    做法是在库里用两种写法独立复算——检查帧数的和不检查的——然后断言
    `core/regime.py` 返回的是**检查帧数**那个数。

    **诚实说明它的局限**：只有在「窗口帧不足的票里恰好有人创新高」的那天，
    两个数才不一样，这条测试才真能区分对错；两个数相同的日子它是过得
    平凡的。所以另外断言了「两数不同时必须等于小的那个」。真正在今天
    就能抓住这类 bug 的是 `tests/test_watch_positions.py` 里的
    `test_new_low_needs_a_full_window_not_just_whatever_bars_exist`——
    那边今天实打实有 6 只日线不足 20 根的票。
    """
    from db import pg

    row = pg.query("""
        with recent as (
            select ticker, date, high,
                   max(high) over (partition by ticker order by date
                       rows between 20 preceding and 1 preceding) as prior_high,
                   count(high) over (partition by ticker order by date
                       rows between 20 preceding and 1 preceding) as prior_bars
            from stock_ohlcv_daily
            where market = 'CN'
              and date > (select max(date) from stock_ohlcv_daily
                          where market = 'CN')::date - 60
        ), latest as (
            select max(date) d from stock_ohlcv_daily where market = 'CN'
        )
        select count(*) filter (where prior_high is not null
                                  and high >= prior_high) as loose,
               count(*) filter (where prior_high is not null
                                  and prior_bars = 20
                                  and high >= prior_high) as strict,
               count(*) filter (where prior_bars < 20) as short_history
        from recent, latest where recent.date = latest.d
    """)[0]

    got = regime.get("CN")["new_highs_20"]
    assert got == row["strict"], (
        f"core/regime.py 返回 {got}，按「窗口必须够 20 根」独立复算是 "
        f'{row["strict"]}（不检查帧数会是 {row["loose"]}）')

    if row["loose"] != row["strict"]:
        assert got != row["loose"], (
            f'今天有窗口不足的票在创新高（两种算法差 '
            f'{row["loose"] - row["strict"]} 只），返回的必须是严格的那个')
    else:
        assert row["short_history"] >= 0  # 记录当天确实无法区分
