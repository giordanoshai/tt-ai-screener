# -*- coding: utf-8 -*-
"""单位约定的回归测试：core/ 交出来的比率字段必须是百分点，不是小数。

## 为什么单独一个文件，为什么必须用真实库数据

这一类错误（库里存 0.164 表示 16.4%，代码却按 16.4 那个量级写阈值）
现有测试一条都抓不住，因为它们喂的是手写的 `profit_margin: 15.0`——
测试里的单位跟生产数据的单位不是一回事，所以换算错了它们照样绿。

实测后果（2026-08-28 收盘）：小数口径下最新交易日 2052 只美股
**全部**落在 `PULLBACK_BAND` 里（「回踩到位」等于没写）；
`dist_ma50_pct` 最负只到 -0.52，`OVERSOLD_DIST_MA50 = -8.0` 永远够不着，
三种短线形态死了一种；全市场 `profit_margin >= 3` 只有 1 只票命中。

所以本文件里的断言全部拿真实数据的**量级**说话。写每一条时的自检是：
把 `core/snapshot._pct_points` 换回 `_f`、把 `core/fundamental._shape`
里的换算删掉，这一行会不会红？会红才留。
"""
import statistics

import pytest

from core import fundamental, scoring, snapshot
from db import pg

#: 复算和库里的值之间允许的误差。库里存了十几位小数，交出去 round 到 2 位，
#: 所以 0.02 是纯粹的四舍五入余量，不是给单位错误留的口子——
#: 差 100 倍的错误比这个大四个数量级。
_ROUND_TOL = 0.02


def _latest_raw_bar(ticker: str, market: str) -> dict:
    """直接从库里拿一只票最新的两根日线。绕开 core/，用来跟快照对账。"""
    rows = pg.query(
        "select date, close, ma_20, atr_14, atr_pct, dist_ma20_pct, pct_chg "
        "from stock_ohlcv_daily where ticker = %s and market = %s "
        "order by date desc limit 2", (ticker, market))
    assert len(rows) == 2, f"{ticker} 在库里不足两根日线，这条测试证明不了什么"
    return rows


def _latest_raw_fundamental(ticker: str, market: str) -> dict:
    row = pg.query_one(
        "select distinct on (ticker) ticker, revenue_growth_yoy, profit_margin,"
        " roe, pe_ratio, beta, short_interest_pct from stock_fundamentals "
        "where ticker = %s and market = %s order by ticker, updated_at desc",
        (ticker, market))
    assert row is not None, f"{ticker} 没有基本面记录，这条测试证明不了什么"
    return row


# ------------------------------------------------ snapshot：全市场量级


def test_snapshot_pct_fields_are_percentage_points():
    """真实数据里，离均线距离必须是百分点量级，不是小数量级。

    这个测试就是为了抓住「库里存小数、代码当百分点」这一类错误：
    如果换算被去掉，全市场的 dist_ma20_pct 会挤在 ±1 以内，下面三条断言全红。
    """
    rows = snapshot.scan("US")
    assert len(rows) > 500, "全市场扫描只回了几只票，下面的统计没有意义"

    d20 = [r["dist_ma20_pct"] for r in rows if r["dist_ma20_pct"] is not None]
    assert len(d20) > 500

    # 1) 量级。实测 2026-08-28 绝对值最大 51.64；换算没生效的话是 0.5164
    assert max(abs(x) for x in d20) > 5, (
        f"全市场离 20 日均线最远的也只有 {max(abs(x) for x in d20):.4f}，"
        "这是小数量级，说明换算没生效")

    # 2) 「回踩到位」得真的筛掉票。小数口径下实测命中率是 100%（等于没筛），
    #    百分点口径下实测 45.9%。85% 这个上界留了足够余量，
    #    但离 100% 还差得远，换算一去掉必然突破。
    band = scoring.PULLBACK_BAND
    in_band = sum(1 for x in d20 if band[0] <= x <= band[1])
    assert in_band / len(d20) < 0.85, (
        f"{in_band}/{len(d20)} 只票落在 PULLBACK_BAND{band} 内，"
        "这个条件等于没写")

    # 3) 日均波动率。实测美股中位数 3.17%；小数口径下是 0.0317
    atr = [r["atr_pct"] for r in rows if r["atr_pct"] is not None]
    assert 1 < statistics.median(atr) < 20, (
        f"atr_pct 中位数 {statistics.median(atr):.4f} 不像百分点")


def test_oversold_bounce_threshold_is_reachable_on_real_data():
    """`OVERSOLD_DIST_MA50 = -8.0` 必须真的有票够得着。

    小数口径下 dist_ma50_pct 最负只到 -0.52，这个阈值永远触发不了，
    `oversold_bounce` 这个形态是死的。换算生效后实测有 299 只票在线下 8% 以上。
    """
    rows = snapshot.scan("US")
    d50 = [r["dist_ma50_pct"] for r in rows if r["dist_ma50_pct"] is not None]
    reachable = [x for x in d50 if x < scoring.OVERSOLD_DIST_MA50]
    assert reachable, (
        f"全市场 {len(d50)} 只票，没有一只跌到 50 日均线下方 "
        f"{abs(scoring.OVERSOLD_DIST_MA50)}%，这个阈值形同虚设")


# ------------------------------- snapshot：两条取数路径都得换算


@pytest.mark.parametrize("ticker", ["AAPL", "NVDA", "MSFT"])
def test_snapshot_get_path_pct_fields_match_prices_in_the_same_dict(ticker):
    """`get()` 交出的 `_pct` 值，必须能用同一份快照里的价格复算出来。

    复算只用快照自己的 close / ma_20 / ma_50 / atr_14，不看库里那几列，
    所以这条断言钉死的是**量纲**：换算被去掉时快照给 0.0318，
    而 (close-ma_20)/ma_20*100 = 3.1753，差 100 倍，直接红。
    """
    snap = snapshot.get([ticker], "US")[ticker]
    assert snap is not None

    assert snap["dist_ma20_pct"] == pytest.approx(
        (snap["close"] - snap["ma_20"]) / snap["ma_20"] * 100, abs=_ROUND_TOL)
    assert snap["dist_ma50_pct"] == pytest.approx(
        (snap["close"] - snap["ma_50"]) / snap["ma_50"] * 100, abs=_ROUND_TOL)
    assert snap["atr_pct"] == pytest.approx(
        snap["atr_14"] / snap["close"] * 100, abs=_ROUND_TOL)


def test_snapshot_scan_path_pct_fields_match_prices_in_the_same_dict():
    """`scan()` 是另一条取数路径，也必须换算过。

    两条路径现在共用 `_enrich()`，但这条测试不假设这一点——
    真有人给 `scan()` 单独拼一份 dict 而忘了换算，这里会红。
    """
    rows = [r for r in snapshot.scan("US")
            if r["close"] and r["ma_20"] and r["atr_14"]][:200]
    assert len(rows) > 100, "样本太少，说明扫描本身出了问题"

    for r in rows:
        assert r["dist_ma20_pct"] == pytest.approx(
            (r["close"] - r["ma_20"]) / r["ma_20"] * 100, abs=_ROUND_TOL), \
            f"{r['ticker']} 的 dist_ma20_pct 跟自己的价格对不上"
        assert r["atr_pct"] == pytest.approx(
            r["atr_14"] / r["close"] * 100, abs=_ROUND_TOL), \
            f"{r['ticker']} 的 atr_pct 跟自己的价格对不上"


# ------------------------- snapshot：已经是百分点的字段不许再乘一次


@pytest.mark.parametrize("ticker", ["AAPL", "NVDA", "MSFT"])
def test_snapshot_pct_chg_is_not_multiplied_again(ticker):
    """`pct_chg` 库里存的已经是百分点，再乘 100 就成了「当日涨 457%」。

    同一张表混了两套单位，这一条防的是「一刀切全乘 100」那种改法。
    复算用前一日收盘价，跟库里那一列无关。
    """
    bars = _latest_raw_bar(ticker, "US")
    expected = (bars[0]["close"] - bars[1]["close"]) / bars[1]["close"] * 100

    snap = snapshot.get([ticker], "US")[ticker]
    assert snap["pct_chg"] == pytest.approx(expected, abs=0.05), (
        f"{ticker} 快照 pct_chg={snap['pct_chg']}，按前一日收盘复算是 "
        f"{expected:.4f}")


@pytest.mark.parametrize("ticker", ["AAPL", "NVDA", "MSFT"])
def test_snapshot_self_computed_pct_fields_are_not_multiplied_again(ticker):
    """`dist_from_20d_high_pct` 和 `ma_50_slope_pct` 是本模块自己算的，
    公式里已经带了 * 100。把它们也塞进换算就会变成 100 倍。

    两条都拿库里的原始价格复算，不给「再乘一次」留任何余地——
    用「斜率绝对值小于某个数」那种松界是不行的：50 日均线 5 天的斜率本来就
    只有零点几，乘 100 之后也才几十，松界照样绿。
    """
    snap = snapshot.get([ticker], "US")[ticker]

    expected_high = (snap["close"] - snap["high_20"]) / snap["high_20"] * 100
    assert snap["dist_from_20d_high_pct"] == pytest.approx(
        expected_high, abs=_ROUND_TOL)

    # 50 日均线跟 5 根之前比。直接从库里取那两根的 ma_50 复算。
    ma50 = [r["ma_50"] for r in pg.query(
        "select date, ma_50 from stock_ohlcv_daily "
        "where ticker = %s and market = %s order by date desc limit 6",
        (ticker, "US"))]
    assert len(ma50) == 6 and all(x is not None for x in ma50)
    expected_slope = (ma50[0] - ma50[5]) / ma50[5] * 100
    assert snap["ma_50_slope_pct"] == pytest.approx(expected_slope, abs=0.01)


def test_snapshot_dist_ma20_and_dist_from_20d_high_are_the_same_magnitude():
    """`analyze_ticker` 的 trend 块把这两个 `_pct` 并排返回给用户看。
    修复前一个是 -0.0138、另一个是 -4.84，量级差 100 倍，读的人必被误导。"""
    rows = [r for r in snapshot.scan("US")
            if r["dist_ma20_pct"] is not None
            and r["dist_from_20d_high_pct"] is not None]
    assert len(rows) > 500

    spread_20 = max(abs(r["dist_ma20_pct"]) for r in rows)
    spread_high = max(abs(r["dist_from_20d_high_pct"]) for r in rows)
    # 同一个量纲的两列，跨度不该差一个数量级以上
    assert 0.1 < spread_20 / spread_high < 10, (
        f"dist_ma20_pct 跨度 {spread_20:.3f}、dist_from_20d_high_pct 跨度 "
        f"{spread_high:.3f}，两个都叫 _pct 却不是一个量纲")


# ------------------------------------------------ fundamental


def test_fundamental_ratio_fields_are_percentage_points():
    """真实基本面里，利润率必须是百分点量级。

    实测 profit_margin：AAPL 27.6、MSFT 40.3、NVDA 63.7。
    换算被去掉的话是 0.276 这种，`> 1` 全部失败。
    """
    out = fundamental.get(["NVDA", "AAPL", "MSFT"], "US")

    for ticker in ("NVDA", "AAPL", "MSFT"):
        row = out[ticker]
        assert row is not None, f"{ticker} 没有基本面数据，这条测试证明不了什么"
        margin = row["profit_margin"]
        assert margin is not None and margin > 1, (
            f"{ticker} 净利率 {margin}，这是小数量级，说明换算没生效")

    growths = [out[t]["revenue_growth_yoy"] for t in ("NVDA", "AAPL", "MSFT")]
    assert max(g for g in growths if g is not None) > 10, (
        f"三只大盘成长股营收增速最高只有 {max(growths)}，"
        "这是小数量级（真实值 NVDA 105.9、MSFT 17.7、AAPL 16.4）")


def test_fundamental_short_interest_is_renamed_to_shares():
    """`short_interest_pct` 这个列名在撒谎：存的是股数不是百分比
    （全市场中位 712 万股、最大 2.94 亿股）。留着原名调用方就会当百分比显示。"""
    row = fundamental.get(["NVDA"], "US")["NVDA"]
    assert "short_interest_pct" not in row, "会骗人的原键不该再出现"
    assert "short_interest_shares" in row
    # 是股数就一定远大于任何一个「百分比」该有的样子
    assert row["short_interest_shares"] > 1000


def test_fundamental_converts_exactly_by_one_hundred():
    """跟库里那一行直接对账：比率乘 100，倍数和分值原样不动。

    这一条同时防两个方向的错：漏换算（比率还是小数），
    以及换过头（把市盈率也乘 100，27.5 倍变成 2750 倍）。
    """
    raw = _latest_raw_fundamental("NVDA", "US")
    row = fundamental.get(["NVDA"], "US")["NVDA"]

    for col in ("revenue_growth_yoy", "profit_margin", "roe"):
        assert row[col] == pytest.approx(raw[col] * 100, abs=0.01), (
            f"{col} 库里 {raw[col]}，交出去应该是 {raw[col] * 100}")

    # 倍数不是百分比，一乘就废
    assert row["pe_ratio"] == pytest.approx(raw["pe_ratio"], abs=0.001)
    assert row["beta"] == pytest.approx(raw["beta"], abs=0.001)
    # 股数更不能乘
    assert row["short_interest_shares"] == pytest.approx(
        raw["short_interest_pct"], abs=0.001)


# ------------------------------------------------ 评分：真实数据上的区分度


def test_score_mid_discriminates_on_real_data():
    """两家真实公司，基本面差距明显的，中线分必须拉开。

    挑票依据（2026-08-28 实测，从 35 只大盘股里跑出来的）：
      - NVDA：营收 +105.9%、利润 +127.8%、净利率 63.7%、ROE 117.2%，
        `score_mid` 的基本面 35 分几乎拿满。
      - KHC：营收 -1.4%、净利率 -13.6%、ROE -8.76%，在亏钱，
        基本面那 35 分只拿到利润增长一项。
    两只票都是流动性好的美股大盘，不是挑出来的极端样本。

    这一对是**特意挑的**：换算生效时两者 78 vs 58（差 20），
    把 core/fundamental 的换算删掉后两者都是 55（差 0）——
    因为小数口径下 NVDA 的 1.059 / 0.637 一档都够不着，
    跟 KHC 的负数拉不开距离。所以这条断言真的守着换算，
    不是靠 NVDA 趋势好蒙对的。
    """
    tickers = ["NVDA", "KHC"]
    snaps = snapshot.get(tickers, "US")
    funds = fundamental.get(tickers, "US")
    for t in tickers:
        assert snaps[t] is not None and funds[t] is not None, \
            f"{t} 缺数据，这条测试证明不了什么"

    strong = scoring.score_mid(snaps["NVDA"], funds["NVDA"], None)
    weak = scoring.score_mid(snaps["KHC"], funds["KHC"], None)

    assert strong["score"] - weak["score"] >= 10, (
        f"NVDA（营收 {funds['NVDA']['revenue_growth_yoy']}%、净利率 "
        f"{funds['NVDA']['profit_margin']}%）得 {strong['score']}，"
        f"KHC（营收 {funds['KHC']['revenue_growth_yoy']}%、净利率 "
        f"{funds['KHC']['profit_margin']}%）得 {weak['score']}，"
        "基本面差这么多分却拉不开，说明基本面那 35 分没起作用")


def test_score_mid_reasons_print_growth_at_the_right_magnitude():
    """给用户看的那句话里，营收增速的数必须是真的。

    修复前 `f"营收同比增长 {revenue:.1f}%"` 会把 NVDA 的 +105.9% 印成
    「1.1%」——不是排版问题，是给交易者看的数字本身就是错的。
    """
    snap = snapshot.get(["NVDA"], "US")["NVDA"]
    fund = fundamental.get(["NVDA"], "US")["NVDA"]
    assert snap is not None and fund is not None

    line = next((r for r in scoring.score_mid(snap, fund, None)["reasons"]
                 if "营收同比增长" in r), None)
    assert line is not None, "NVDA 的中线理由里没有营收增速这句"

    printed = float(line.split("营收同比增长 ")[1].split("%")[0])
    # NVDA 真实营收增速是三位数；印成 1.1 就是把小数当百分点了
    assert printed > 10, f"印出来的营收增速是 {printed}%，量级不对"
    assert printed == pytest.approx(fund["revenue_growth_yoy"], abs=0.05)
