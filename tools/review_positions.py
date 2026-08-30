# -*- coding: utf-8 -*-
"""工具 1：持仓分析——我手上这几只票现在什么情况，该不该动。

## 排序按紧急度，不按盈亏

按盈亏排序的话，最该处理的票（刚跌破止损、后天财报）可能排在第五位，
而排第一的只是「涨得最多」——那个不需要你做任何事。这个工具的价值在于
**把需要动手的排在最前面**。

## 汇总按币种分开

用户同时做美股和 A股。把美元市值和人民币市值加起来得到的数字没有任何意义，
所以 summary 是 `{"USD": {...}, "CNY": {...}}`，没有 total 这个键。

## 期权流走实时

只有几只持仓，实时接口 3.6 秒一只、并发跑完 4 秒左右，值得。
库里的期权数据一天只灌两次，拿它看持仓等于看昨天。取不到就自动退回库里的，
并在结果里标明这一路是缓存数据（`flow.source == "db"`）。
"""
import json

from core import catalyst, cn_signals, regime, scoring, snapshot
from db import pg

#: 紧急度分档。数字本身不重要，重要的是顺序：
#: 跌破止损 > 财报临近 > 重大负面消息 > 期权大幅看空 > 正常。
URGENCY = {
    "stop_breached": 90,
    "earnings_soon": 70,
    "bad_news": 65,
    "stop_breached_recovering": 60,   # 早已跌破，但已站回 20 日均线
    "bearish_flow": 55,
    "cn_outflow": 55,
    "normal": 20,
}

#: 财报还有几天算「临近」。3 个交易日，用 5 个自然日近似（跨周末）。
EARNINGS_SOON_DAYS = 5

#: 期权净溢价偏空到这个金额（美元）才值得报，再小就是噪音。
BEARISH_FLOW_THRESHOLD = 500_000

#: A股主力 5 日净流出到这个金额（元）才值得报。
CN_OUTFLOW_THRESHOLD = 100_000_000

_POSITIONS_SQL = """
select p.ticker, p.market,
       coalesce(p.currency,
                case p.market when 'CN' then 'CNY' else 'USD' end) as currency,
       p.avg_cost, p.shares, p.updated_at,
       m.company_name
from user_positions p
left join stocks_meta m
       on m.ticker = p.ticker
      and m.market = p.market
order by p.ticker
"""


def _num(value):
    return None if value is None else float(value)


def _flags_and_urgency(stop: dict, earnings: dict | None,
                       news: list[dict], flow: dict | None,
                       cn_flow: dict | None,
                       lhb_rows: list[dict],
                       snap: dict | None = None) -> tuple[list[str], int]:
    """把一堆数字翻成「要不要动手」。返回人话标签和紧急度。"""
    flags, urgency = [], URGENCY["normal"]

    if stop.get("breached"):
        if snap and snap.get("above_ma20"):
            # 已经站回 20 日均线：止损是早就破了的历史事实，仓位在修复。
            # 跟「今天刚破位」按同一个最高档报警，会把真正紧急的那几只淹掉
            flags.append("已跌破当初止损，但已站回 20 日均线")
            urgency = max(urgency, URGENCY["stop_breached_recovering"])
        else:
            flags.append("跌破止损")
            urgency = max(urgency, URGENCY["stop_breached"])

    days_until = (earnings or {}).get("days_until")
    if days_until is not None and 0 <= days_until <= EARNINGS_SOON_DAYS:
        flags.append(f"{days_until} 天后财报")
        urgency = max(urgency, URGENCY["earnings_soon"])

    bad = [n for n in news
           if (n.get("sentiment_label") or "").lower() == "negative"
           and n.get("significant") == "High"]
    if bad:
        flags.append("有重大负面消息")
        urgency = max(urgency, URGENCY["bad_news"])

    if flow and flow.get("net_premium", 0) < -BEARISH_FLOW_THRESHOLD:
        flags.append("期权资金大幅看空")
        urgency = max(urgency, URGENCY["bearish_flow"])

    if cn_flow:
        net_5d = cn_flow.get("net_5d") or 0
        if net_5d < -CN_OUTFLOW_THRESHOLD:
            flags.append(f"主力 5 日净流出 {abs(net_5d) / 1e8:.1f} 亿")
            urgency = max(urgency, URGENCY["cn_outflow"])

    if lhb_rows:
        flags.append(f"近期上过龙虎榜（{len(lhb_rows)} 次）")

    return flags, urgency


def _one(row: dict, snap: dict | None, stop: dict, earnings: dict | None,
         news: list[dict], flow: dict | None, cn_flow: dict | None,
         lhb_rows: list[dict]) -> dict:
    shares = _num(row["shares"]) or 0.0
    avg_cost = _num(row["avg_cost"]) or 0.0
    price = (snap or {}).get("close")

    gaps = []
    if snap is None:
        gaps.append("没有行情数据")
    elif snap.get("stale"):
        gaps.append(f"行情已经 {snap['days_since_bar']} 天没更新")
    if earnings is None:
        gaps.append("没有财报日数据")
    if row["market"] == "US" and (flow is None or flow.get("alert_count") == 0):
        gaps.append("没有期权异动数据")
    if flow and flow.get("source") == "db":
        gaps.append("期权数据取自数据库（实时接口没取到），一天只更新两次")
    if row["market"] == "CN" and cn_flow is None:
        gaps.append("没有主力资金数据")

    flags, urgency = _flags_and_urgency(stop, earnings, news, flow,
                                        cn_flow, lhb_rows, snap)

    return {
        "ticker": row["ticker"],
        "market": row["market"],
        "currency": row["currency"],
        "company_name": row.get("company_name"),
        "shares": shares,
        "avg_cost": avg_cost,
        "price": price,
        "market_value": round(price * shares, 2) if price else None,
        "pnl_pct": round((price - avg_cost) / avg_cost * 100, 2)
                   if price and avg_cost else None,
        "pnl_amount": round((price - avg_cost) * shares, 2)
                      if price and avg_cost else None,
        "trend": {
            "above_ma20": (snap or {}).get("above_ma20"),
            "above_ma50": (snap or {}).get("above_ma50"),
            "above_ma200": (snap or {}).get("above_ma200"),
            "rsi": (snap or {}).get("rsi_14"),
            "vol_ratio": (snap or {}).get("vol_ratio"),
            "atr_pct": (snap or {}).get("atr_pct"),
            "dist_from_20d_high_pct": (snap or {}).get("dist_from_20d_high_pct"),
        },
        "stop": stop,
        "earnings": earnings or {},
        "news": news,
        "flow": flow if row["market"] == "US" else {
            "main_net_today": (cn_flow or {}).get("net_amount"),
            "main_net_5d": (cn_flow or {}).get("net_5d"),
            "lhb_hits": len(lhb_rows),
        },
        "flags": flags,
        "urgency": urgency,
        "data_gaps": gaps,
    }


def _summarize(positions: list[dict]) -> dict:
    """按币种汇总。**没有 total**——见模块注释。"""
    buckets: dict[str, dict] = {}
    for pos in positions:
        currency = pos["currency"]
        block = buckets.setdefault(currency, {
            "market_value": 0.0, "cost_basis": 0.0, "pnl_amount": 0.0,
            "pnl_pct": None, "count": 0, "largest_position_pct": None,
            "largest_position": None,
        })
        block["count"] += 1
        if pos["market_value"]:
            block["market_value"] += pos["market_value"]
        block["cost_basis"] += pos["avg_cost"] * pos["shares"]
        if pos["pnl_amount"]:
            block["pnl_amount"] += pos["pnl_amount"]

    for currency, block in buckets.items():
        cost = block["cost_basis"]
        block["market_value"] = round(block["market_value"], 2)
        block["cost_basis"] = round(cost, 2)
        block["pnl_amount"] = round(block["pnl_amount"], 2)
        block["pnl_pct"] = round(block["pnl_amount"] / cost * 100, 2) if cost else None
        same = [p for p in positions
                if p["currency"] == currency and p["market_value"]]
        if same and block["market_value"]:
            biggest = max(same, key=lambda p: p["market_value"])
            block["largest_position"] = biggest["ticker"]
            block["largest_position_pct"] = round(
                biggest["market_value"] / block["market_value"] * 100, 2)
    return buckets


def review_positions(ticker: str | None = None) -> str:
    """我手上这几只票现在什么情况，该不该动。

    给每只持仓算：现在赚多少赔多少、技术面站在哪、止损该放哪、有没有跌破、
    几天后财报、近期有没有重大消息、期权市场在往哪押（A股换成主力资金和
    龙虎榜）。然后**按需要动手的紧急程度排序**——跌破止损的排最前面，
    只是涨得多的排后面。

    组合汇总按币种分开算，美元和人民币不会加在一起。

    ticker: 只看某一只票时填，一般不填，默认看全部持仓
    """
    try:
        rows = pg.query(_POSITIONS_SQL)
        if ticker:
            wanted = ticker.upper().strip()
            rows = [r for r in rows if r["ticker"] == wanted]
        if not rows:
            return json.dumps({"as_of": None, "positions": [], "summary": {},
                               "warnings": [], "data_gaps": [],
                               "message": "没有持仓" if not ticker
                                          else f"持仓里没有 {ticker}"},
                              ensure_ascii=False, indent=2, default=str)

        by_market: dict[str, list[str]] = {}
        for row in rows:
            by_market.setdefault(row["market"], []).append(row["ticker"])

        snaps, earnings, news, flows = {}, {}, {}, {}
        cn_flows, lhbs = {}, {}
        warnings = []

        for market, tickers in by_market.items():
            snaps.update(snapshot.get(tickers, market))
            earnings.update(catalyst.earnings_for(tickers, market))
            news.update(catalyst.news_for(tickers, days=7,
                                          min_significance="Medium",
                                          per_ticker=3))
            if market == "US":
                flows.update(catalyst.options_flow_live(tickers))
            else:
                cn_flows.update(cn_signals.fund_flow(tickers))
                lhbs.update(cn_signals.lhb(tickers, days=30))

            fresh = regime.freshness(market)
            if fresh["stale"]:
                warnings.append(
                    f"{market} 行情最新只到 {fresh['latest_trading_day']}，"
                    f"距今 {fresh['days_since']} 天。"
                    f"最近失败的采集任务：{fresh['failed_jobs'] or '无'}")
            if fresh["partial_jobs"]:
                warnings.append(
                    f"{market} 有采集任务只跑了一半（数据写了，某个可选源没取到）："
                    f"{fresh['partial_jobs']}")

        positions = []
        for row in rows:
            code = row["ticker"]
            snap = snaps.get(code)
            stop = (scoring.position_stop(_num(row["avg_cost"]), snap)
                    if snap else {"suggested_stop": None, "basis": None,
                                  "distance_pct": None, "breached": None,
                                  "forward_stop": None,
                                  "gaps": ["没有行情，算不出止损"]})
            positions.append(_one(
                row, snap, stop, earnings.get(code), news.get(code, []),
                flows.get(code), cn_flows.get(code), lhbs.get(code, [])))

        positions.sort(key=lambda p: (-p["urgency"], p["pnl_pct"] or 0,
                                      p["ticker"]))
        as_of = max((s["date"] for s in snaps.values() if s), default=None)

        return json.dumps({
            "as_of": as_of,
            "positions": positions,
            "summary": _summarize(positions),
            "warnings": warnings,
            "data_gaps": sorted({g for p in positions for g in p["data_gaps"]}),
        }, ensure_ascii=False, indent=2, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
