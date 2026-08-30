# -*- coding: utf-8 -*-
"""日线技术面快照。5 个工具里有 4 个要它。

## 这个模块只管技术面，这是硬边界

短波段只看「价格怎么走 + 钱往哪押 + 有没有雷」，公司好不好一概不看
（设计文档第三节）。所以这里的 SQL **不查任何估值和增长列**——市盈率、
营收增长、利润率、分析师目标价，一个都不碰。想要基本面去调
`core/fundamental.py`，那个模块只被中线档调用。

这不是靠自觉：`tests/test_sql_rules.py` 会扫这个文件的源码，
出现 `BANNED_FUNDAMENTAL_COLUMNS` 里任何一个词就直接红。

## 指标不用自己算

`stock_ohlcv_daily` 里 `ma_20/50/200`、`rsi_14`、`vol_ratio`、`atr_14`、
`atr_pct`、`high_20`、`high_55`、`dist_ma20_pct`、`dist_ma50_pct` 全是
tt-data-hub 预先算好的。自己再算一遍只会算出跟别处不一致的数。

## 两种取数方式，别用错

- `get()`：几只票。按票 `order by date desc limit 8` 走 (ticker, date) 主键
  索引倒扫，实测每票 1.8 千代价，顺带保住停牌退市的票（它们最后一根 K 线
  不是最新交易日，但仍然拿得到）。
- `scan()`：全市场。先单独查出该市场的最新交易日，再等值比较。
  **绝不能**写成 `date = (select max(date) ... where ticker = o.ticker)`
  这种相关子查询，那是并行全表扫 380 万行（实测代价 17.6 万）。

两条路径都经过 `_enrich()` 拼快照，所以单位换算只需要在那一个地方做。
`scan()` 事后覆盖的三个字段（`prior_high_20`、`had_vol_up_3d`、
`ma_50_slope_pct`）都不在换算名单里，不会绕过换算。

## 单位约定：交出去的 `_pct` 一律是百分点

**上游 `stock_ohlcv_daily` 同一张表里混了两套单位**（2026-08-28 实测）：

- `dist_ma20_pct`、`dist_ma50_pct`、`atr_pct` 存的是**小数**。
  AAPL 那天存 0.0317529865，手算 `(close-ma_20)/ma_20` 也是 0.031753，
  意思是「高于 20 日均线 3.18%」。
- `pct_chg` 存的却是**百分点**。同一天 NVDA 存 -4.575，意思是「当日跌 4.57%」。

这个不一致坑过一次：`core/scoring.py` 的 `PULLBACK_BAND = (-3.0, 2.0)` 是按
百分点写的，直接拿小数去比，最新交易日 2052 只美股**全部**落在区间内，
「回踩到位」这个条件等于没写；`OVERSOLD_DIST_MA50 = -8.0` 则永远不可能被触发，
因为 `dist_ma50_pct` 最负也只到 -0.52。

所以本模块定下这条约定：

> **凡是 `core/snapshot.py` 交出去的、名字带 `_pct` 的值，一律是百分点。**

`_enrich()` 用 `_pct_points()` 把上面那三个小数字段乘 100。**另外三个 `_pct`
字段保持原样，绝对不能再乘一次**：

- `pct_chg`：库里存的就已经是百分点。
- `ma_50_slope_pct`：本模块自己算的，公式里已经带了 `* 100`。
- `dist_from_20d_high_pct`：同上，本模块自己算的，已经带了 `* 100`。

代码里这三处各有一行注释盯着。再乘一次的话，NVDA 当日 -4.57% 会变成 -457%。

## 这条约定的边界

**只管 `core/snapshot.py` 和 `core/fundamental.py` 的输出。**
`skills/__init__.py`（`:281` 写的是 `dist_ma20_pct < 0.08`）和 `web/app.py`
直接查库、按小数在用，那是自洽的，不要跟着改。

`tools/screening.py` 原本也在这一类里，但它是**对外的 MCP 工具**，跟
`analyze_ticker` 同框出现时同名字段差 100 倍会让人算错，所以任务 11B 修复轮
把它一并对齐到百分点了。判断标准是「这个字段会不会以工具结果的形式交到
使用者手上」：会，就用百分点；只在内部 SQL 里跟库比大小，就保持小数。
"""
import datetime

from db import pg

#: 短波段禁用的基本面列。这个文件里出现任何一个都是 bug，回归测试会拦。
BANNED_FUNDAMENTAL_COLUMNS = frozenset({
    "pe_ratio", "ps_ratio", "pb_ratio", "peg_ratio", "forward_pe",
    "ev_to_ebitda", "revenue_growth_yoy", "earnings_growth_yoy",
    "gross_margin", "operating_margin", "profit_margin", "roe",
    "fcf_yield", "analyst_rating", "analyst_target_price", "analyst_count",
    "dividend_yield",
})

MARKETS = ("US", "CN")

#: 最新一根 K 线离今天超过这么多个自然日，就算行情陈旧。
#: 3 天能盖住正常的周末（周五收盘到周一早上是 3 天）。
STALE_DAYS = 3

#: 取几根日线。8 根够算：前一日的 20 日高点（判突破）、近 3 日有没有放量阳线
#: （判超跌反弹）、5 天前的 50 日均线（判中线趋势是不是在往上走）。
_BARS = 8

#: 近 3 日的「放量阳线」判据：收盘高于开盘，且量能不低于平时的 1.2 倍。
_VOL_UP_RATIO = 1.2

#: 美股流动性门槛：20 日均额 1000 万美元。实测过线的有 1860 只。
#: A股：5000 万人民币，且排除 ST（设计文档第四节）。
DEFAULT_MIN_DOLLAR_VOLUME = {"US": 10_000_000.0, "CN": 50_000_000.0}

_BAR_COLUMNS = """
    d.date, d.open, d.close, d.pct_chg, d.volume,
    d.ma_20, d.ma_50, d.ma_200, d.rsi_14, d.vol_ratio, d.vol_ma_20,
    d.atr_14, d.atr_pct, d.high_20, d.high_55,
    d.dist_ma20_pct, d.dist_ma50_pct
"""

_LATEST_DAY_SQL = """
select max(date) as day
from stock_ohlcv_daily
where market = %s
  and date >= current_date - 30
"""

# 每只票走一次索引倒扫，取最近 8 根。停牌的票也能拿到它自己最后一根。
# 顺带把公司名带出来——工具层要拿它显示，不然每个工具都得自己再查一次 stocks_meta。
_GET_SQL = f"""
select t.ticker, m.company_name, o.*
from unnest(%s::text[]) as t(ticker)
left join stocks_meta m
       on m.ticker = t.ticker
      and m.market = %s
left join lateral (
    select {_BAR_COLUMNS}
    from stock_ohlcv_daily d
    where d.ticker = t.ticker
      and d.market = %s
    order by d.date desc
    limit {_BARS}
) o on true
order by t.ticker, o.date desc
"""

# 全池扫描。先拿最新交易日（上面那条小查询），再等值比较，实测 109 毫秒。
# 时间窗收到 20 个自然日，够算 5 天前的 50 日均线，又不会扫太多行。
_SCAN_SQL_HEAD = f"""
with recent as (
    select d.ticker,
           {_BAR_COLUMNS},
           lag(d.high_20) over w as prior_high_20,
           lag(d.ma_50, 5) over w as ma_50_prev5,
           max(case when d.close > d.open and d.vol_ratio >= {_VOL_UP_RATIO}
                    then 1 else 0 end)
               over (partition by d.ticker order by d.date
                     rows between 2 preceding and current row) as vol_up_3d,
           row_number() over (partition by d.ticker
                              order by d.date desc) as rn
    from stock_ohlcv_daily d
    where d.market = %s
      and d.date >= %s
    window w as (partition by d.ticker order by d.date)
)
"""

_SCAN_SQL_US = _SCAN_SQL_HEAD + """
select r.*, m.company_name
from recent r
join ticker_over20b u on u.ticker = r.ticker
left join stocks_meta m on m.ticker = r.ticker and m.market = 'US'
where r.rn = 1
  and r.date = %s
  and r.close * r.vol_ma_20 >= %s
"""

_SCAN_SQL_CN = _SCAN_SQL_HEAD + """
select r.*, u.name as company_name
from recent r
join cn_universe u on u.ticker = r.ticker
where r.rn = 1
  and r.date = %s
  and not u.is_st
  and u.avg_amount_20d >= %s
"""


def _check_market(market: str) -> str:
    market = (market or "").upper()
    if market not in MARKETS:
        raise ValueError(f"market 只能是 US 或 CN，收到 {market!r}")
    return market


def _f(value):
    return None if value is None else float(value)


def _pct_points(value):
    """库里这三个字段存的是小数（0.0318 = 3.18%），本模块一律按百分点交出去。
    同一张表的 pct_chg 存的已经是百分点，不要再乘。"""
    v = _f(value)
    return None if v is None else round(v * 100, 2)


def latest_trading_day(market: str) -> datetime.date | None:
    """该市场最新有日线的那一天。全池扫描前先拿它，别写相关子查询。"""
    row = pg.query_one(_LATEST_DAY_SQL, (_check_market(market),))
    return row["day"] if row else None


def _enrich(bars: list[dict], ticker: str, market: str,
            today: datetime.date) -> dict | None:
    """把一只票最近 8 根日线压成一份快照。bars 已按日期倒序。"""
    if not bars or bars[0].get("date") is None:
        return None

    last = bars[0]
    close = _f(last["close"])
    ma_20, ma_50, ma_200 = _f(last["ma_20"]), _f(last["ma_50"]), _f(last["ma_200"])
    high_20, high_55 = _f(last["high_20"]), _f(last["high_55"])
    vol_ma_20 = _f(last["vol_ma_20"])

    # 前一日的 20 日高点：判「突破」要跟昨天的高点比，跟今天比永远不成立
    prior_high_20 = _f(bars[1]["high_20"]) if len(bars) > 1 else None

    # 50 日均线在不在往上走：跟 5 根之前比。
    # 这里自己乘了 100，交出去已经是百分点，不用再过 _pct_points
    ma_50_slope_pct = None
    if len(bars) > 5 and ma_50:
        past = _f(bars[5]["ma_50"])
        if past:
            ma_50_slope_pct = round((ma_50 - past) / past * 100, 3)

    # 近 3 日有没有出现过放量阳线
    had_vol_up_3d = any(
        _f(b["close"]) is not None and _f(b["open"]) is not None
        and _f(b["close"]) > _f(b["open"])
        and (_f(b["vol_ratio"]) or 0) >= _VOL_UP_RATIO
        for b in bars[:3])

    # 近 20 日内是不是创过新高：20 日高点就等于 55 日高点，说明这个高点
    # 是最近 20 天内做出来的。省掉一次额外查询。
    made_20d_high_recently = bool(
        high_20 and high_55 and high_20 >= high_55 * 0.999)

    days_since_bar = (today - last["date"]).days

    return {
        "ticker": ticker,
        "market": market,
        "company_name": last.get("company_name"),
        "date": last["date"],
        "close": close,
        "open": _f(last["open"]),
        # pct_chg 库里存的就已经是百分点（-4.575 = 当日跌 4.57%），不能再乘 100
        "pct_chg": _f(last["pct_chg"]),
        "volume": _f(last["volume"]),
        "dollar_volume": round(close * vol_ma_20, 2)
                         if close and vol_ma_20 else None,
        "ma_20": ma_20,
        "ma_50": ma_50,
        "ma_200": ma_200,
        "above_ma20": None if ma_20 is None else close > ma_20,
        "above_ma50": None if ma_50 is None else close > ma_50,
        "above_ma200": None if ma_200 is None else close > ma_200,
        "rsi_14": _f(last["rsi_14"]),
        "vol_ratio": _f(last["vol_ratio"]),
        "atr_14": _f(last["atr_14"]),
        # 下面三个库里存的是小数，统一换成百分点交出去（见模块 docstring）
        "atr_pct": _pct_points(last["atr_pct"]),
        "high_20": high_20,
        "high_55": high_55,
        "dist_ma20_pct": _pct_points(last["dist_ma20_pct"]),
        "dist_ma50_pct": _pct_points(last["dist_ma50_pct"]),
        # 本模块自己算的，公式里已经带了 * 100，别再过 _pct_points
        "dist_from_20d_high_pct": round((close - high_20) / high_20 * 100, 2)
                                  if close and high_20 else None,
        "prior_high_20": prior_high_20,
        "ma_50_slope_pct": ma_50_slope_pct,
        "had_vol_up_3d": had_vol_up_3d,
        "made_20d_high_recently": made_20d_high_recently,
        "stale": days_since_bar > STALE_DAYS,
        "days_since_bar": days_since_bar,
    }


def get(tickers: list[str], market: str) -> dict[str, dict | None]:
    """取一批票的技术面快照。没有行情的票值是 None，但键一定在。"""
    market = _check_market(market)
    tickers = [t.upper().strip() for t in (tickers or []) if t and t.strip()]
    if not tickers:
        return {}

    rows = pg.query(_GET_SQL, (tickers, market, market))
    today = datetime.date.today()

    grouped: dict[str, list[dict]] = {t: [] for t in tickers}
    for row in rows:
        grouped.setdefault(row["ticker"], []).append(row)

    return {t: _enrich(grouped.get(t, []), t, market, today) for t in tickers}


def scan(market: str, min_dollar_volume: float | None = None) -> list[dict]:
    """全市场扫描，只留过了流动性门槛的票。"""
    market = _check_market(market)
    day = latest_trading_day(market)
    if day is None:
        return []

    threshold = (min_dollar_volume if min_dollar_volume is not None
                 else DEFAULT_MIN_DOLLAR_VOLUME[market])
    window_start = day - datetime.timedelta(days=20)
    sql = _SCAN_SQL_US if market == "US" else _SCAN_SQL_CN

    today = datetime.date.today()
    out = []
    for row in pg.query(sql, (market, window_start, day, threshold)):
        # scan 的窗口函数已经把三个派生值算好了，直接塞进去，
        # 免得 _enrich 因为只拿到一行而把它们判成 None
        snap = _enrich([row], row["ticker"], market, today)
        if snap is None:
            continue
        snap["prior_high_20"] = _f(row.get("prior_high_20"))
        snap["had_vol_up_3d"] = bool(row.get("vol_up_3d"))
        ma_50, prev5 = snap["ma_50"], _f(row.get("ma_50_prev5"))
        snap["ma_50_slope_pct"] = (round((ma_50 - prev5) / prev5 * 100, 3)
                                   if ma_50 and prev5 else None)
        out.append(snap)
    return out
