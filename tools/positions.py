"""持仓工具。数据从自建 Postgres 读，采集那一摊已经搬到 tt-data-hub。

这一版最重要的改动是**分币种**：用户同时做美股和 A股，美股按美元计价、
A股按人民币计价。把两边的市值和盈亏加在一起算出来的数字没有任何意义，
所以汇总是按币种拆开的一组数，不是一个总数。

移植时踩过的三个坑，改这个文件的时候别再踩回去：

1. 占位符是 ``%s``，不是 DuckDB 的 ``?``。
2. ``user_positions`` 的主键**只有 ticker 一列**，所以 upsert 的冲突键只能写
   ``on conflict (ticker)``。写成 ``(ticker, market)`` 会直接报
   ``no unique or exclusion constraint matching the ON CONFLICT specification``。
   代价是同一个代码不能同时在两个市场持仓——美股和 A股的代码格式完全不同
   （AAPL vs 600519.SH），实际撞不上。
3. 取最新一天不要写会扫全表的形式。日线表 275 万行，按市场 ``group by`` 求
   max(date) 实测是并行全表扫（执行计划代价 17.6 万）；按票走主键索引倒扫
   只要 1.8 千，而且停牌、退市的票也能拿到它自己最后一根 K 线。
"""
import json
from collections import defaultdict

from db import pg

# 市场 -> 计价货币。持仓表的 currency 列就是按这个填的。
CURRENCY_BY_MARKET = {"US": "USD", "CN": "CNY"}

# 每只票取它自己最新的一根日线：走 (ticker, date) 主键索引倒扫 limit 1。
# 持仓一般就几只到几十只，这里是几十次索引查找，比全表求 max(date) 便宜两个数量级。
# ``as_of`` 带出来是为了让行情过期这件事看得见——A股数据还没进库之前，
# CN 持仓的现价会是空的。
LIST_SQL = """
select
    p.ticker,
    p.market,
    coalesce(p.currency,
             case p.market when 'CN' then 'CNY' else 'USD' end) as currency,
    m.company_name,
    m.sector,
    p.avg_cost,
    p.shares,
    o.close,
    o.date as as_of,
    case when p.avg_cost > 0
         then round(((o.close - p.avg_cost) / p.avg_cost * 100)::numeric, 2)
    end as pnl_pct,
    case when p.avg_cost > 0
         then round(((o.close - p.avg_cost) * p.shares)::numeric, 2)
    end as pnl_amount,
    o.rsi_14,
    o.ma_50,
    o.ma_200,
    p.updated_at
from user_positions p
left join stocks_meta m
       on m.ticker = p.ticker
      and m.market = p.market
left join lateral (
    select d.date, d.close, d.rsi_14, d.ma_50, d.ma_200
    from stock_ohlcv_daily d
    where d.ticker = p.ticker
      and d.market = p.market
    order by d.date desc
    limit 1
) o on true
order by pnl_amount desc nulls last, p.ticker
"""


def _currency_of(row: dict) -> str:
    """这一行按什么货币计价。currency 列允许为空，空了就按市场兜底。"""
    return row.get("currency") or CURRENCY_BY_MARKET.get(row.get("market"), "USD")


def _summarize(rows: list[dict]) -> dict:
    """按币种分别汇总。美元和人民币绝不放进同一个桶里加。"""
    buckets = defaultdict(lambda: {"count": 0, "value": 0.0, "cost": 0.0})
    for row in rows:
        bucket = buckets[_currency_of(row)]
        close = float(row.get("close") or 0)
        shares = float(row.get("shares") or 0)
        avg_cost = float(row.get("avg_cost") or 0)
        bucket["count"] += 1
        bucket["value"] += close * shares
        bucket["cost"] += avg_cost * shares

    summary = {}
    for currency, bucket in buckets.items():
        pnl = bucket["value"] - bucket["cost"]
        summary[currency] = {
            "count": bucket["count"],
            "total_value": round(bucket["value"], 2),
            "total_cost": round(bucket["cost"], 2),
            "total_pnl": round(pnl, 2),
            "total_pnl_pct": (round(pnl / bucket["cost"] * 100, 2)
                              if bucket["cost"] > 0 else None),
        }
    return summary


def list_positions() -> str:
    """列出全部持仓：现价、盈亏、关键技术指标。汇总按币种分开。

    返回的 summary 是 ``{"USD": {...}, "CNY": {...}}`` 这种形式，
    没有跨币种的总数——那种数字算出来也没法看。
    """
    try:
        rows = pg.query(LIST_SQL)
        return json.dumps({"positions": rows, "summary": _summarize(rows)},
                          ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def add_position(ticker: str, avg_cost: float, shares: float,
                 market: str = "US") -> str:
    """新增或更新一笔持仓。同一个代码再加一次就是覆盖，不会出现两行。

    ticker:   股票代码。A股写成 600519.SH 这种带交易所后缀的形式
    avg_cost: 平均成本价，按该市场的货币计
    shares:   持股数量
    market:   'US' 或 'CN'，决定这笔持仓按什么货币计价
    """
    try:
        ticker = (ticker or "").upper().strip()
        market = (market or "").upper().strip()
        if not ticker:
            return json.dumps({"error": "ticker 不能为空"}, ensure_ascii=False)
        if market not in CURRENCY_BY_MARKET:
            return json.dumps({"error": "market 只能是 US 或 CN"},
                              ensure_ascii=False)
        if avg_cost is None or avg_cost <= 0:
            return json.dumps({"error": "avg_cost 必须大于 0"}, ensure_ascii=False)
        if shares is None or shares <= 0:
            return json.dumps({"error": "shares 必须大于 0"}, ensure_ascii=False)

        currency = CURRENCY_BY_MARKET[market]

        known = pg.query_one(
            "select ticker from stocks_meta where ticker = %s and market = %s",
            (ticker, market))
        if not known:
            return json.dumps(
                {"error": f"{market} 市场里没有 {ticker} 这只票。"
                          f"等 tt-data-hub 下次采集补上元数据再试。"},
                ensure_ascii=False)

        # 主键只有 ticker，所以换市场会把原来那笔顶掉。撞车概率接近零，
        # 但真发生了得让用户看见，不能悄悄覆盖。
        previous = pg.query_one(
            "select market from user_positions where ticker = %s", (ticker,))
        replaced_market = (previous or {}).get("market")
        if replaced_market == market:
            replaced_market = None

        pg.execute_write("""
            insert into user_positions
                (ticker, market, avg_cost, shares, currency, updated_at)
            values (%s, %s, %s, %s, %s, now())
            on conflict (ticker) do update set
                market = excluded.market,
                avg_cost = excluded.avg_cost,
                shares = excluded.shares,
                currency = excluded.currency,
                updated_at = now()
        """, (ticker, market, avg_cost, shares, currency))

        result = {"ok": True, "ticker": ticker, "market": market,
                  "currency": currency, "avg_cost": avg_cost, "shares": shares}
        if replaced_market:
            result["replaced_market"] = replaced_market
            result["warning"] = (
                f"{ticker} 原来是 {replaced_market} 市场的持仓，已被这次的 "
                f"{market} 持仓顶掉。持仓表主键只有代码一列，"
                f"同一个代码不能同时在两个市场。")
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def remove_position(ticker: str) -> str:
    """删掉一笔持仓。"""
    try:
        ticker = (ticker or "").upper().strip()
        removed = pg.fetch_write(
            "delete from user_positions where ticker = %s returning ticker",
            (ticker,))
        if not removed:
            return json.dumps({"error": f"{ticker} 不在持仓里"},
                              ensure_ascii=False)
        return json.dumps({"ok": True, "removed": ticker}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def analyze_positions() -> str:
    """对全部持仓做综合分析：技术面、基本面、新闻、盈亏。至少要有一笔持仓。

    market 会一起传给分析层——A股和美股的估值口径、交易时段都不一样，
    分析的时候不能当成一回事。
    """
    try:
        rows = pg.query(
            "select ticker, market, avg_cost, shares from user_positions "
            "order by updated_at")
        if not rows:
            return json.dumps({"error": "还没有持仓，先添加几笔。"},
                              ensure_ascii=False)

        from skills import gather_context

        payload = [{"ticker": r["ticker"],
                    "market": r["market"],
                    "cost_basis": float(r["avg_cost"]),
                    "shares": float(r["shares"])} for r in rows]
        return gather_context("position_review", {"positions": payload})
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
