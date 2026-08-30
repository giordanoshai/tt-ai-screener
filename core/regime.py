# -*- coding: utf-8 -*-
"""市场环境和数据新鲜度。

## 美股和 A股不是同一套算法，因为数据不一样

美股有现成的 `market_breadth_daily`，但**它是标普 500 口径**——实测
advances 223 + declines 273 ≈ 500，不是全市场 2000 多只。返回里要写明这一点，
不然读的人会以为「涨跌家数」说的是整个美股。

A股没有这张表，当场从日线算：一天 1500 行，几十毫秒。顺带把涨停家数、
炸板率从 `cn_limit_pool_daily` 捞出来——A股是情绪市，涨停家数决定今天该不该
出手，这个信息美股那边没有对应物。

## 新鲜度检测

设计文档第八节第三条：最新交易日距今超过 3 天要加陈旧警告，并附上
`ingest_runlog` 里最后几次采集的状态。用户能立刻知道是「今天没行情」
还是「采集挂了」。
"""
import datetime

from core import snapshot
from db import pg

_US_BREADTH_SQL = """
select date, pct_above_ma20, pct_above_ma50, pct_above_ma200,
       advances, declines, ad_ratio, new_highs_20, new_lows_20,
       breadth_score, breadth_level
from market_breadth_daily
order by date desc
limit 1
"""

#: 「创 N 个交易日新高」的 N，以及为了算它要往回捞多少个自然日。
#: 21 个交易日（当天 + 前 20 个）在 A股大约是 30 个自然日，遇上长假会更长，
#: 所以窗口开到 60 个自然日，留一倍余量。
NEW_HIGH_WINDOW = 20
_NEW_HIGH_LOOKBACK_DAYS = 60

# A股当场算。当天 1500 行；为了算「创 20 个交易日新高」要多捞几天的行，
# 走 (market, date) 过滤 + (ticker, date) 索引，实测仍是百毫秒级。
#
# **`new_highs_20` 的窗口不能把当天算进去**（评审 C3，跟
# `tools/watch_positions.py` 的「创 20 个交易日新低」是同一个 bug）：
# 早期写法是 `close >= high_20`，而库里的 `high_20` 这一列**包含当天**
# （`core/snapshot.py` 专门为此准备了 `prior_high_20`，注释写着「跟今天的
# 比永远不成立，今天的高点包含今天」）。数学上 `close ≤ 当日 high ≤
# high_20`，所以这个条件只在「收盘价正好等于当日最高价、而且这个最高价
# 恰好是 20 日最高」时才成立。2026-08-28 实测（1500 只 A股）：
# 这么写命中 155 只，按真实语义（当日最高价创出前 20 个交易日以来的新高）
# 应该命中 316 只。
#
# 现在用 `rows between 20 preceding and 1 preceding` 取**当天之前**那 20
# 根日线的最高价，跟当天的 high 比。
_CN_BREADTH_SQL = f"""
with recent as (
    select ticker, date, close, high, pct_chg, ma_20, ma_50, ma_200,
           max(high) over (partition by ticker order by date
                           rows between {NEW_HIGH_WINDOW} preceding
                                    and 1 preceding) as prior_high,
           -- 窗口帧不足时 max() 照样给值（只有 3 根就拿 3 根的最高价），
           -- 那样刚上市的票会被算成「创 {NEW_HIGH_WINDOW} 日新高」。
           -- 数一下帧里到底有几根，不够就不算数。
           count(high) over (partition by ticker order by date
                             rows between {NEW_HIGH_WINDOW} preceding
                                      and 1 preceding) as prior_bars
    from stock_ohlcv_daily
    where market = 'CN'
      and date > %s::date - {_NEW_HIGH_LOOKBACK_DAYS}
      and date <= %s
)
select count(*) as total,
       count(*) filter (where pct_chg > 0) as advances,
       count(*) filter (where pct_chg < 0) as declines,
       round(100.0 * count(*) filter (where close > ma_20)
             / nullif(count(*) filter (where ma_20 is not null), 0), 2)
           as pct_above_ma20,
       round(100.0 * count(*) filter (where close > ma_50)
             / nullif(count(*) filter (where ma_50 is not null), 0), 2)
           as pct_above_ma50,
       round(100.0 * count(*) filter (where close > ma_200)
             / nullif(count(*) filter (where ma_200 is not null), 0), 2)
           as pct_above_ma200,
       count(*) filter (where prior_high is not null
                          and prior_bars = {NEW_HIGH_WINDOW}
                          and high >= prior_high) as new_highs_20
from recent
where date = %s
"""

_CN_LIMIT_SQL = """
select max(snapshot_date) as date,
       count(*) as limit_up_count,
       count(*) filter (where open_times > 0) as broken_count,
       max(limit_streak) as max_streak
from cn_limit_pool_daily
where snapshot_date = (select max(snapshot_date) from cn_limit_pool_daily)
"""

_RUNLOG_SQL = """
select job, market, status, rows_written, finished_at
from ingest_runlog
where started_at >= now() - interval '48 hours'
  and (market = %s or market is null)
order by started_at desc
limit 12
"""


def _level(ad_ratio, pct_above_ma50) -> str:
    """把两个数翻成一句人话：现在是能出手的环境，还是该收手。"""
    if ad_ratio is None or pct_above_ma50 is None:
        return "UNKNOWN"
    if ad_ratio >= 1.5 and pct_above_ma50 >= 60:
        return "BULLISH"
    if ad_ratio <= 0.6 or pct_above_ma50 <= 30:
        return "BEARISH"
    return "MIXED"


def _f(value):
    return None if value is None else float(value)


def _empty(market: str, reason: str, source: str) -> dict:
    """没数据时的兜底。**每个契约字段都要在**，只是值为 None。

    调用方拿到的字典形状必须跟正常路径一致——不然它在「缺数据」这个
    最该稳的场景下反而会 KeyError，正好跟降级的目的相反。
    """
    out = {
        "market": market, "date": None,
        "advances": None, "declines": None, "ad_ratio": None,
        "pct_above_ma20": None, "pct_above_ma50": None, "pct_above_ma200": None,
        "new_highs_20": None, "new_lows_20": None,
        "level": "UNKNOWN", "source": source, "note": reason,
        "gaps": [reason],
    }
    if market == "CN":
        out.update({"total": None, "limit_up_count": None,
                    "broken_count": None, "broken_rate": None,
                    "max_limit_streak": None})
    return out


def get(market: str) -> dict:
    """当前市场环境。美股读宽度表，A股当场算。"""
    market = (market or "").upper()
    if market not in snapshot.MARKETS:
        raise ValueError(f"market 只能是 US 或 CN，收到 {market!r}")

    if market == "US":
        row = pg.query_one(_US_BREADTH_SQL)
        if not row:
            return _empty("US", "没有市场宽度数据", "market_breadth_daily")
        return {
            "market": "US",
            "date": row["date"],
            "advances": row["advances"],
            "declines": row["declines"],
            "ad_ratio": _f(row["ad_ratio"]),
            "pct_above_ma20": _f(row["pct_above_ma20"]),
            "pct_above_ma50": _f(row["pct_above_ma50"]),
            "pct_above_ma200": _f(row["pct_above_ma200"]),
            "new_highs_20": row["new_highs_20"],
            "new_lows_20": row["new_lows_20"],
            "level": row["breadth_level"] or _level(_f(row["ad_ratio"]),
                                                    _f(row["pct_above_ma50"])),
            "source": "market_breadth_daily",
            "note": "这是标普 500 口径的涨跌家数，不是全部美股",
            "gaps": [],
        }

    day = snapshot.latest_trading_day("CN")
    if day is None:
        return _empty("CN", "没有 A股行情", "stock_ohlcv_daily")

    row = pg.query_one(_CN_BREADTH_SQL, (day, day, day)) or {}
    advances = row.get("advances") or 0
    declines = row.get("declines") or 0
    ad_ratio = round(advances / declines, 2) if declines else None

    gaps = []
    limit_row = pg.query_one(_CN_LIMIT_SQL) or {}
    limit_date = limit_row.get("date")
    limit_up = broken = broken_rate = None
    if limit_date is None:
        # 表里一行都没有。注意不能用 `not limit_up` 判断——count(*) 在
        # 匹配不到行时返回的是 0 不是 None，会把「真的零涨停」也吞进来
        gaps.append("没有涨停板数据")
    else:
        limit_up = limit_row.get("limit_up_count") or 0
        broken = limit_row.get("broken_count") or 0
        broken_rate = round(broken / limit_up, 3) if limit_up else None
        if limit_date != day:
            # 涨停采集落后于日线采集。不说破的话，两天的数据会被贴上
            # 同一个日期标签摆在一起
            gaps.append(f"涨停数据停留在 {limit_date}，比行情日 {day} 旧")

    return {
        "market": "CN",
        "date": day,
        "total": row.get("total"),
        "advances": advances,
        "declines": declines,
        "ad_ratio": ad_ratio,
        "pct_above_ma20": _f(row.get("pct_above_ma20")),
        "pct_above_ma50": _f(row.get("pct_above_ma50")),
        "pct_above_ma200": _f(row.get("pct_above_ma200")),
        "new_highs_20": row.get("new_highs_20"),
        "new_lows_20": None,
        "limit_up_count": limit_up,
        "broken_count": broken,
        "broken_rate": broken_rate,
        "max_limit_streak": limit_row.get("max_streak"),
        "level": _level(ad_ratio, _f(row.get("pct_above_ma50"))),
        "source": "stock_ohlcv_daily",
        "note": "涨跌家数按库里有日线的 A股算；涨停数据来自 cn_limit_pool_daily",
        "gaps": gaps,
    }


def freshness(market: str) -> dict:
    """行情新不新鲜，采集有没有出事。陈旧时工具要在返回里加警告。"""
    day = snapshot.latest_trading_day(market)
    days_old = (datetime.date.today() - day).days if day else None
    jobs = [dict(row) for row in pg.query(_RUNLOG_SQL, (market.upper(),))]
    failed = [j for j in jobs if j["status"] == "failed"]
    partial = [j for j in jobs if j["status"] == "partial"]
    return {
        "market": market.upper(),
        "latest_trading_day": day,
        "days_since": days_old,
        "stale": bool(days_old is not None and days_old > snapshot.STALE_DAYS),
        "recent_jobs": jobs,
        # 分开报：failed 是「什么都没写出来」，partial 是「写了，但某个
        # 可选数据源没取到」。混在一起会让真正的失败淹没在噪音里。
        # 按任务名去重：同一个任务 48 小时内跑两次都 partial，报两遍没有增量信息
        "failed_jobs": sorted({f"{j['job']}({j['status']})" for j in failed}),
        "partial_jobs": sorted({f"{j['job']}({j['status']})" for j in partial}),
    }
