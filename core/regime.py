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

# A股当场算。一天 1500 行，走 (ticker, date) 索引，很便宜。
_CN_BREADTH_SQL = """
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
       count(*) filter (where high_20 is not null
                          and close >= high_20) as new_highs_20
from stock_ohlcv_daily
where market = 'CN'
  and date = %s
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


def get(market: str) -> dict:
    """当前市场环境。美股读宽度表，A股当场算。"""
    market = (market or "").upper()
    if market not in snapshot.MARKETS:
        raise ValueError(f"market 只能是 US 或 CN，收到 {market!r}")

    if market == "US":
        row = pg.query_one(_US_BREADTH_SQL)
        if not row:
            return {"market": "US", "date": None, "level": "UNKNOWN",
                    "source": "market_breadth_daily", "note": "",
                    "gaps": ["没有市场宽度数据"]}
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
        return {"market": "CN", "date": None, "level": "UNKNOWN",
                "source": "stock_ohlcv_daily", "note": "",
                "gaps": ["没有 A股行情"]}

    row = pg.query_one(_CN_BREADTH_SQL, (day,)) or {}
    advances = row.get("advances") or 0
    declines = row.get("declines") or 0
    ad_ratio = round(advances / declines, 2) if declines else None

    gaps = []
    limit_row = pg.query_one(_CN_LIMIT_SQL) or {}
    limit_up = limit_row.get("limit_up_count")
    if not limit_up:
        gaps.append("没有涨停板数据")
    broken = limit_row.get("broken_count") or 0
    broken_rate = round(broken / limit_up, 3) if limit_up else None

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
    jobs = [dict(row) for row in pg.query(_RUNLOG_SQL)]
    failed = [j for j in jobs if j["status"] in ("failed", "partial")]
    return {
        "market": market.upper(),
        "latest_trading_day": day,
        "days_since": days_old,
        "stale": bool(days_old is not None and days_old > snapshot.STALE_DAYS),
        "recent_jobs": jobs,
        "failed_jobs": [f"{j['job']}({j['status']})" for j in failed],
    }
