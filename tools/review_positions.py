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

## 「跌破止损」分三种，判定跟盯盘工具共用同一个函数（评审 C1）

早期这里只用「有没有站回 20 日均线」降一档，没有「现在是盈是亏」和
「破了多久」这两个维度。2026-08-30 真实输出：RKLB（−23.35%）、
GOOGL（−5.04%）、NVO（**+3.66%**）、AMZN（**+8.30%**）四只全都是
`urgency: 90`、flag 一律「跌破止损」——AMZN 的跌破幅度只有 **0.17%**，
而且还在浮盈。同一天 `watch_positions` 对 AMZN 给的是「移动止损被触及 /
严重度 40 / 这笔仓位现在还是浮盈 204 USD」。**两个工具是同一个用户同一天
连着调的，用户先读到哪个就信哪个。**

现在两个工具都调 `core/scoring.classify_stop_breach()`，分到哪一类是同一个
答案（`trailing_touched` / `breached_recent` / `breached_longstanding`），
每只持仓的 `stop_state` 字段把这个答案直接写出来。档位名称和措辞两边仍然
不一样（一个是「紧急度」、一个是「严重度」，语境不同），但不可能再出现
一个说「最高紧急度、按纪律该清掉」、另一个说「还在浮盈、不必马上动」。

## 每一路取数各自 try/except（评审 C2）

行情、财报、新闻、期权流、A股资金、龙虎榜、止损历史、新鲜度——每一路
各自包在 try/except 里，挂了只把这一路写进 `data_gaps`，其余照常给结论。
以前是整个函数一个 try：任何一路抛异常，返回体就只剩 `{"error": ...}`，
一个结论都不给。跟 `watch_positions.py` / `detect_catalysts.py` 的约定一致。
"""
import json

from core import catalyst, cn_signals, regime, scoring, snapshot
from db import pg

#: 紧急度分档。数字本身不重要，重要的是顺序：
#: 最近跌破止损 > 财报临近 > 重大负面消息 > 期权大幅看空 >
#: 长期在止损位下方 > 浮盈中碰到移动止损线 > 正常。
#:
#: 后两档的相对位置跟 `watch_positions.SEVERITY` 保持一致（那边
#: `stop_breached_longstanding` 是 50、`trailing_stop_touched` 浅档是 40，
#: 都远低于 `stop_breached` 的 95/75）——同一只票在两个工具里的紧急程度
#: 必须读起来是同一个意思（评审 C1）。
URGENCY = {
    "stop_breached": 90,
    "earnings_soon": 70,
    "bad_news": 65,
    "bearish_flow": 55,
    "cn_outflow": 55,
    "stop_breached_longstanding": 50,   # 仍在浮亏，但破位是持仓状态不是新信号
    "trailing_stop_touched": 40,        # 还在浮盈，只是碰到了护利润的移动止损
    "normal": 20,
}

#: 把 `core/scoring` 的三分类翻成本模块的紧急度档位名。
_STOP_STATE_URGENCY = {
    scoring.STOP_STATE_TRAILING: "trailing_stop_touched",
    scoring.STOP_STATE_RECENT: "stop_breached",
    scoring.STOP_STATE_LONGSTANDING: "stop_breached_longstanding",
}

#: 财报还有几天算「临近」。3 个交易日，用 5 个自然日近似（跨周末）——
#: `earnings_for()` 给的 `days_until` 是自然日，所以常量也写成自然日。
#: 三个工具的窗口不一样、各自的理由见 `watch_positions.EARNINGS_SOON_DAYS`
#: 的注释（评审 I2）。
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


def _round2(value):
    """价格类数字只保留 2 位小数交出去。原样透传的话十几位小数
    （`"price": 64.3899993896484`）直接糊到用户脸上（同
    `tools/watch_positions.py` 和 `tools/detect_catalysts.py` 的 `_round2`）。"""
    return None if value is None else round(float(value), 2)


def _consecutive_breach_days(avg_cost, daily_rows):
    """连续多少个交易日在止损位下方。本体在 `core/scoring`，跟
    `watch_positions` 共用（评审 C1）；这里留一层同名封装方便测试替换。"""
    return scoring.consecutive_breach_days(avg_cost, daily_rows)


def _stop_flag(state: str, consecutive_days: int, pnl_amount: float | None,
               stop: dict, currency: str = "") -> str:
    """把三分类翻成一句用户看得懂的话。措辞跟 `watch_positions` 的告警
    文案对应（那边是「移动止损被触及 / 止损被击穿 / 已经连续 N 个交易日
    在止损位下方」），说的是同一件事。"""
    if state == scoring.STOP_STATE_TRAILING:
        return (f"跌破了护利润的移动止损线（依据 {stop.get('basis')}），"
                f"但这笔仓位还是浮盈"
                + (f" {abs(pnl_amount):.0f} {currency}".rstrip()
                   if pnl_amount is not None else ""))
    if state == scoring.STOP_STATE_LONGSTANDING:
        return (f"已经连续 {consecutive_days} 个交易日在止损位下方，"
                f"是持仓状态不是今天的新信号")
    return ("跌破止损"
            + (f"（已经连续 {consecutive_days} 个交易日）"
               if consecutive_days > 1 else ""))


def _flags_and_urgency(stop: dict, earnings: dict | None,
                       news: list[dict], flow: dict | None,
                       cn_flow: dict | None,
                       lhb_rows: list[dict],
                       snap: dict | None = None,
                       pnl_amount: float | None = None,
                       stop_rows: list[dict] | None = None,
                       avg_cost: float | None = None,
                       currency: str = ""
                       ) -> tuple[list[str], int, str | None]:
    """把一堆数字翻成「要不要动手」。返回人话标签、紧急度、止损状态。"""
    flags, urgency = [], URGENCY["normal"]
    stop_state = None

    if stop.get("breached"):
        # 三分法：浮盈时是「移动止损被触及」、最近跌破是「止损被击穿」、
        # 长期在止损位下方是「持仓状态不是新信号」。判定函数跟
        # `watch_positions` 是同一个，两个工具不可能再打架（评审 C1）
        consecutive_days, _ = _consecutive_breach_days(
            avg_cost, stop_rows or [])
        stop_state = scoring.classify_stop_breach(
            pnl_amount, consecutive_days, scoring.STOP_BREACH_RECENT_DAYS)
        flags.append(_stop_flag(stop_state, consecutive_days,
                                pnl_amount, stop, currency))
        urgency = max(urgency, URGENCY[_STOP_STATE_URGENCY[stop_state]])

    days_until = (earnings or {}).get("days_until")
    if days_until is not None and 0 <= days_until <= EARNINGS_SOON_DAYS:
        flags.append(f"{days_until} 天后财报")
        urgency = max(urgency, URGENCY["earnings_soon"])

    # 大盘综述不是这只票的专属消息，不许它把紧急度抬起来（评审 I3）。
    # 真实案例：ORCL 的「有重大负面消息」来自一条挂了 8 只票的
    # 《大型科技公司AI支出规模远超预期》，把紧急度从 20 抬到了 65
    bad = [n for n in news
           if (n.get("sentiment_label") or "").lower() == "negative"
           and n.get("significant") == "High"
           and not n.get("market_wide")]
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

    return flags, urgency, stop_state


def _shape_news(item: dict) -> dict:
    """一条新闻压成持仓视角需要的最小形态。

    早期是把 `catalyst.news_for()` 的整条记录原样塞进每只票下面——同一条
    新闻挂了 GOOGL 和 AMZN 两只持仓，标题、英文标题、url、以及它挂的全部
    票的数组，就在两只票下面各复制一整份。这正是 `detect_catalysts.py`
    花一整轮修掉的模式，也顶着项目最硬的规矩：工具只返回算好的结论，
    不把原始表倒给 Claude。

    现在只留结论需要的字段，`tickers` 数组换成一个计数 + 一个
    `market_wide` 判定（一条新闻挂了几只票是有用的信息，那一长串代码
    本身不是）。
    """
    tickers = item.get("tickers") or []
    return {
        "title": item.get("title"),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "sentiment_label": item.get("sentiment_label"),
        "significant": item.get("significant"),
        # 这条消息一共挂了几只票，以及够不够多到该当成大盘综述看
        "ticker_count": len(tickers),
        "market_wide": catalyst.is_market_wide(item),
    }


def _one(row: dict, snap: dict | None, stop: dict, earnings: dict | None,
         news: list[dict], flow: dict | None, cn_flow: dict | None,
         lhb_rows: list[dict], stop_rows: list[dict] | None = None) -> dict:
    shares = _num(row["shares"]) or 0.0
    avg_cost = _num(row["avg_cost"]) or 0.0
    price = (snap or {}).get("close")
    pnl_amount = (round((price - avg_cost) * shares, 2)
                  if price and avg_cost else None)

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

    shaped_news = [_shape_news(item) for item in news]
    flags, urgency, stop_state = _flags_and_urgency(
        stop, earnings, shaped_news, flow, cn_flow, lhb_rows, snap,
        pnl_amount=pnl_amount, stop_rows=stop_rows, avg_cost=avg_cost,
        currency=row["currency"])

    return {
        "ticker": row["ticker"],
        "market": row["market"],
        "currency": row["currency"],
        "company_name": row.get("company_name"),
        "shares": shares,
        "avg_cost": avg_cost,
        "price": _round2(price),
        "market_value": round(price * shares, 2) if price else None,
        "pnl_pct": round((price - avg_cost) / avg_cost * 100, 2)
                   if price and avg_cost else None,
        "pnl_amount": pnl_amount,
        "trend": {
            "above_ma20": (snap or {}).get("above_ma20"),
            "above_ma50": (snap or {}).get("above_ma50"),
            "above_ma200": (snap or {}).get("above_ma200"),
            "rsi": _round2((snap or {}).get("rsi_14")),
            "vol_ratio": _round2((snap or {}).get("vol_ratio")),
            "atr_pct": _round2((snap or {}).get("atr_pct")),
            "dist_from_20d_high_pct": _round2(
                (snap or {}).get("dist_from_20d_high_pct")),
        },
        "stop": stop,
        # 「跌破止损」到底是三种情况里的哪一种。判定跟 `watch_positions`
        # 共用同一个函数，两个工具对同一只票不会给出打架的结论（评审 C1）
        "stop_state": stop_state,
        "earnings": earnings or {},
        "news": shaped_news,
        "flow": flow if row["market"] == "US" else {
            "main_net_today": (cn_flow or {}).get("net_amount"),
            "main_net_5d": (cn_flow or {}).get("net_5d"),
            "lhb_hits": len(lhb_rows),
        },
        "flags": flags,
        "urgency": urgency,
        "data_gaps": gaps,
    }


def _freshness_warnings(market: str, fresh: dict) -> list[str]:
    """把 `regime.freshness()` 的结论翻成人话警告。

    拆成纯函数是为了能测——真实数据哪天行情不陈旧、采集任务都正常，端到端
    测这条逻辑就会全绿，测不出「陈旧警告被删掉了」这种改坏
    （`detect_catalysts.py` / `watch_positions.py` 里有同名的同款函数）。

    失败和「只跑了一半」要无条件报，不能挂在 stale 下面——那样
    「行情是新的但某个采集任务挂了」会完全不出声，而那恰恰最该说。
    """
    out = []
    if fresh["stale"]:
        out.append(f"{market} 行情最新只到 {fresh['latest_trading_day']}，"
                   f"距今 {fresh['days_since']} 天")
    if fresh["failed_jobs"]:
        out.append(f"{market} 有采集任务失败：{fresh['failed_jobs']}")
    if fresh["partial_jobs"]:
        out.append(f"{market} 有采集任务只跑了一半（数据写了，某个可选源"
                   f"没取到）：{fresh['partial_jobs']}")
    return out


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
        cn_flows, lhbs, stop_rows = {}, {}, {}
        warnings: list[str] = []
        source_gaps: list[str] = []

        # 每一路各自 try/except：挂了只丢这一路，其余照常给结论（评审 C2）。
        # 以前是整个函数一个 try——新闻、期权流、新鲜度任意一路抛异常，
        # 用户拿到的就只有一个 {"error": ...}，一条持仓结论都没有
        def _try(label: str, fn, default):
            try:
                return fn()
            except Exception as exc:
                source_gaps.append(f"{label}拉取失败：{exc}")
                return default

        for market, tickers in by_market.items():
            snaps.update(_try(f"{market} 行情快照",
                              lambda: snapshot.get(tickers, market), {}))
            earnings.update(_try(f"{market} 财报日",
                                 lambda: catalyst.earnings_for(tickers, market),
                                 {}))
            news.update(_try(
                f"{market} 新闻",
                lambda: catalyst.news_for(tickers, days=7,
                                          min_significance="Medium",
                                          per_ticker=3), {}))
            stop_rows.update(_try(
                f"{market} 止损历史",
                lambda: snapshot.stop_history(tickers, market), {}))
            if market == "US":
                flows.update(_try("期权异动",
                                  lambda: catalyst.options_flow_live(tickers),
                                  {}))
            else:
                cn_flows.update(_try("A股主力资金",
                                     lambda: cn_signals.fund_flow(tickers), {}))
                lhbs.update(_try("龙虎榜",
                                 lambda: cn_signals.lhb(tickers, days=30), {}))

            fresh = _try(f"{market} 行情新鲜度",
                         lambda: regime.freshness(market), None)
            if fresh:
                warnings.extend(_freshness_warnings(market, fresh))

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
                flows.get(code), cn_flows.get(code), lhbs.get(code, []),
                stop_rows.get(code, [])))

        positions.sort(key=lambda p: (-p["urgency"], p["pnl_pct"] or 0,
                                      p["ticker"]))
        # 停牌退市的票最后一根 K 线不是最新交易日，取 max 而不是取第一只
        as_of = max((s["date"] for s in snaps.values() if s), default=None)

        return json.dumps({
            "as_of": as_of,
            "positions": positions,
            "summary": _summarize(positions),
            "warnings": warnings,
            "data_gaps": sorted({g for p in positions for g in p["data_gaps"]}
                                | set(source_gaps)),
        }, ensure_ascii=False, indent=2, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
