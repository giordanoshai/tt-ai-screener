"""自选池工具。数据源是 Postgres（原来是本地 DuckDB 文件）。

移植时踩过的几个坑，都写在这儿免得下次再踩：

1. 线上表的时间列叫 ``created_at``，DuckDB 那版叫 ``added_at``，照搬会报列不存在。
2. ``user_watchlist.user_id`` 是非空 uuid，插入时必须带上，值取自
   ``config.WATCHLIST_USER_ID``。``id`` 是自增列，插入时不要给。
3. 这张表有两个唯一索引：``(ticker)`` 和 ``(user_id, ticker)``。
   ``on conflict`` 盯的是 ``(ticker)`` ——它是更严的那个，
   任何 ``(user_id, ticker)`` 重复必然也是 ticker 重复，盯它一个就够。
4. 占位符是 ``%s``，不是 DuckDB 的 ``?``。
5. 取最新一根 K 线不能写相关子查询，在 Postgres 上是全表扫 275 万行；
   先用 CTE 把自选票的最新日期一次算出来，再等值连接。
6. 日线表混了美股和 A股，每个连接条件都必须带 ``market``，否则会串市场。
"""
import json

import config
from db import pg
from tools.compact import compact_rows

# 这个工具一度是 12 个里最费 token 的一个：2026-09-03 实测 155 只自选
# 一次返回 66,672 字符（约 22,000 token），比第二名还大三倍。压完 42,001，
# 省 38%。省下的全是格式，信息一点没少——怎么压、为什么这么压，
# 见 `tools/compact.py`。

#: 每个数字字段保留几位小数。**逐个字段写**，量级的坑见 `tools/compact.py`。
#: 这七个都是价格或百分点（`pct_chg` 是 2.61 表示涨 2.61%），所以都能截 2 位。
_DIGITS = {"close": 2, "pct_chg": 2, "rsi_14": 1, "ma_20": 2,
           "ma_50": 2, "ma_200": 2, "vol_ratio": 2}

#: 只留日期的那两列。`created_at` 是 timestamptz，`date` 是 date。
_DATE_ONLY = ("date", "created_at")


# 自选池的行情快照。三层左连接，所以票即使没有基本信息、没有行情，
# 也一定会出现在结果里——自选池是用户自己维护的，不能被数据缺失吞掉。
_LIST_SQL = """
    with wl as (
        select
            w.ticker,
            coalesce(m.market, 'US') as market,
            w.created_at
        from user_watchlist w
        left join stocks_meta m on m.ticker = w.ticker
    ),
    latest as (
        select d.ticker, d.market, max(d.date) as bar_date
        from stock_ohlcv_daily d
        join wl on wl.ticker = d.ticker and wl.market = d.market
        group by d.ticker, d.market
    )
    select
        wl.ticker,
        m.company_name,
        m.sector,
        wl.market,
        o.date,
        o.close,
        o.pct_chg,
        o.rsi_14,
        o.ma_20,
        o.ma_50,
        o.ma_200,
        o.vol_ratio,
        wl.created_at
    from wl
    left join stocks_meta m on m.ticker = wl.ticker
    left join latest l on l.ticker = wl.ticker and l.market = wl.market
    left join stock_ohlcv_daily o
           on o.ticker = l.ticker
          and o.market = l.market
          and o.date = l.bar_date
    order by wl.created_at desc, wl.ticker
"""


def list_watchlist() -> str:
    """列出自选池里全部股票，带最新价、当日涨跌和关键技术指标。

    价格和均线保留 2 位小数、RSI 保留 1 位，时间只到天。
    某只票当天没有行情时，对应字段是 null（票本身照样在列表里）。
    """
    try:
        rows = pg.query(_LIST_SQL)
        return json.dumps(compact_rows(rows, _DIGITS, _DATE_ONLY),
                          ensure_ascii=False, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def add_to_watchlist(ticker: str) -> str:
    """把一只股票加进自选池。已经在池子里的话什么都不做，不报错。"""
    try:
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return json.dumps({"error": "ticker is required"})

        known = pg.query_one(
            "select ticker, market from stocks_meta where ticker = %s",
            (ticker,))
        if not known:
            return json.dumps(
                {"error": f"{ticker} 不在数据库里。等 tt-data-hub 下次采集后再试。"},
                ensure_ascii=False)

        if not config.WATCHLIST_USER_ID:
            return json.dumps({"error": "缺少 WATCHLIST_USER_ID 配置"},
                              ensure_ascii=False)

        inserted = pg.execute_write(
            "insert into user_watchlist (user_id, ticker) values (%s, %s) "
            "on conflict (ticker) do nothing",
            (config.WATCHLIST_USER_ID, ticker))
        return json.dumps({"ok": True, "ticker": ticker,
                           "already_present": inserted == 0})
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def remove_from_watchlist(ticker: str) -> str:
    """把一只股票从自选池移除。"""
    try:
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return json.dumps({"error": "ticker is required"})

        removed = pg.fetch_write(
            "delete from user_watchlist where ticker = %s returning ticker",
            (ticker,))
        if not removed:
            return json.dumps({"error": f"{ticker} 不在自选池里"},
                              ensure_ascii=False)
        return json.dumps({"ok": True, "removed": ticker})
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
