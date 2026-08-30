# -*- coding: utf-8 -*-
"""工具 5：持仓盯盘——我的持仓上有没有需要立刻处理的信号。

## 没事就说没事

不触发就返回空的告警列表加一句「N 只持仓，无异常」。给一堆看着热闹但不用管
的东西，等于没有盯盘——用户会开始忽略它，然后真出事的那天也一起忽略了。

所以门槛定得高：期权异动要单笔溢价 ≥ 50 万美元，或者 score 进这只票近 30 天的
前 10%（实测 NVDA 和 AMZN 的 90 分位都是 60 分）。

## 期权异动按票汇总，不按笔数扇出

**这条是拿真实数据实测出来的，不是猜的。** 用现有 6 只美股持仓、默认
`hours=24`（往回看 2 天）实测：仅 AMZN 一只票就有 39 笔交易过了「单笔
≥ 50 万美元」这条线，8 天窗口下 AMZN 一只票涨到 54 笔。如果一笔对应一条
告警，光期权这一路就能刷出小一百条，跟同目录 `detect_catalysts.py` 那次
「一条新闻按票数扇出成 N 条」是同一类问题，也正撞上这个工具自己的第一条
设计原则——「没事就说没事」，不是「有点动静就说」。

所以这里改成**一只票一条**：把过线的交易按票分组，数出看涨看跌各几笔、
各自的溢价合计、以及金额最大的那一笔，合成一条告警。哪边溢价合计大就按
哪边定调（`big_bullish_flow` / `big_bearish_flow`）。这样「AMZN 这两天
期权很热」只出现一次，而不是同一个结论用 39 种措辞重复 39 遍。

UW 实时警报（`/api/alerts`）同理：同一只票在 100 条里触发好几条自定义规则
时，也合并成一条，把命中的规则名都列出来，不按规则条数扇出。

## 每条告警说清三件事

发生了什么、对我的仓位意味着什么、建议怎么处理。只说「NVDA 出现大额看跌期权」
是没用的——用户还得自己算这对手上 20 股意味着什么。

## UW 实时警报的坑

`/api/alerts` 返回的 `symbol` 是**期权合约代码**（`NVDA261002P00190000`），
不是股票代码；标的藏在 `meta.underlying_symbol` 里。而且 100 条里有 14 条是
`potus_schedule` 这种噪音，`symbol` 直接是 null。拿 symbol 直接跟持仓比对
会一条都对不上，必须先用 uw.underlying_of() 解出标的。

## 实时接口挂了也要能用，某一路挂了也不能连累别的路

价格触发（跌破均线、跌破止损、创新低）和财报临近都是从库里算的，
不依赖任何外部接口。UW 那两路（期权异动、实时警报）取不到就在
`checked_sources` 里少一项，其余照常给——**每一路各自 try/except**，
出错写进 `data_gaps`，不会因为一路挂了把另一路已经拿到的结果一起丢掉
（同目录 `review_positions.py` / `detect_catalysts.py` / `scan_swing.py`
都是这个约定）。

行情新不新鲜、采集任务有没有失败或者只跑一半，也在这里查一次并写进
`warnings`——跟前面几个工具的约定一致，「行情是新的但某个采集任务挂了」
这种情况必须无条件报出来，不能挂在「行情陈旧」判断下面变成潜台词。

## 止损告警的盈亏描述要跟真实盈亏方向一致

`position_stop()` 的止损位在成本上方时会抬到均线（护浮盈），这时候破位是
「浮盈还在但保护线没了」，不是「亏钱了」。2026-08-30 用真实持仓跑出来才
发现：早期写法不分青红皂白一律印「浮亏 {数字}」，AMZN 那天明明浮盈 204
美元，被印成「浮亏 204 USD」；亏钱的几只（GOOGL/MP/ORCL/RKLB）又会印成
「浮亏 -184 USD」这种双重负号。前者是把赚钱的仓位说成在亏钱，后者是让
数字费解——都属于「这份输出会不会误导交易者」要挡住的那一类。现在按
盈亏正负分别措辞，且数字一律取绝对值。

## 返回体大小有上限

`alerts` 按严重度排序后最多返回 `MAX_ALERTS` 条，`alert_count` /
`returned_count` / `truncated` 如实写明总共命中多少条、实际返回多少条，
不会默默截断。加了按票汇总之后，正常情况下用不到这个上限（几只持仓顶多
出几条告警），但持仓只数会变、UW 规则会变，这道上限是留给将来的安全网。
"""
import json

from core import catalyst, cn_signals, regime, scoring, snapshot, uw
from db import pg

#: 单笔期权溢价到这个数（美元）才值得打扰用户
BIG_PREMIUM = 500_000

#: 严重度。数字只用来排序。
SEVERITY = {
    "stop_breached": 95,
    "below_ma50": 80,
    "earnings_soon": 75,
    "big_bearish_flow": 70,
    "below_ma20": 60,
    "new_low": 60,
    "cn_outflow": 65,
    "lhb": 50,
    "big_bullish_flow": 45,
}

#: 财报还有几个交易日算「临近」（5 个交易日 ≈ 7 个自然日）
EARNINGS_SOON_DAYS = 7

#: alerts 最多返回这么多条。见模块 docstring「返回体大小有上限」。
MAX_ALERTS = 50

_POSITIONS_SQL = """
select ticker, market, avg_cost, shares,
       coalesce(currency,
                case market when 'CN' then 'CNY' else 'USD' end) as currency
from user_positions
order by ticker
"""

_FLOW_RECENT_SQL = """
select ticker, opt_type, total_premium, score, sentiment, day,
       strike, expiry
from flow_alerts_archive
where ticker = any(%s)
  and day >= current_date - %s
order by total_premium desc
limit 200
"""

_FLOW_P90_SQL = """
select ticker,
       percentile_cont(0.9) within group (order by score) as p90
from flow_alerts_archive
where ticker = any(%s)
  and day >= current_date - 30
group by ticker
"""

_NEW_LOW_SQL = """
select ticker, min(low) as low_20
from stock_ohlcv_daily
where ticker = any(%s)
  and market = %s
  and date >= current_date - 40
group by ticker
"""


def is_big_flow(row: dict, p90: float | None) -> bool:
    """这笔期权异动够不够大，值不值得报。"""
    premium = row.get("total_premium")
    if premium is not None and float(premium) >= BIG_PREMIUM:
        return True
    score = row.get("score")
    if score is not None and p90 is not None and float(score) >= float(p90):
        return True
    return False


def _alert(ticker, kind, what, means, action) -> dict:
    return {
        "ticker": ticker,
        "kind": kind,
        "what_happened": what,
        "what_it_means": means,
        "suggested_action": action,
        "severity": SEVERITY.get(kind, 30),
    }


def _freshness_warnings(market: str, fresh: dict) -> list[str]:
    """把 `regime.freshness()` 的结论翻成人话警告。拆成纯函数是为了能测——
    真实数据哪天行情不陈旧、采集任务都正常，端到端测这条逻辑就会全绿，
    测不出「陈旧警告被删掉了」这种改坏（`detect_catalysts.py` 踩过这个坑）。
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


def watch_positions(hours: int = 24) -> str:
    """我的持仓上有没有需要立刻处理的信号。

    四路一起看：价格触发（跌破均线、跌破止损、创 20 日新低）、财报临近、
    期权异动（A股换成主力资金和龙虎榜）、突发消息，外加 UW 实时警报流里
    命中持仓的部分。

    **没事就说没事**——不触发就返回空列表加一句话，不制造噪音。
    每条告警都会说清楚：发生了什么、对你的仓位意味着什么、建议怎么处理。
    同一只票的多笔期权异动会合并成一条，不会因为一只票交易活跃就刷屏。

    价格触发和财报临近不依赖任何外部接口，UW 那两路（期权异动、实时警报）
    挂了不连累其它路，会在 `checked_sources` 里少一项、在 `data_gaps`
    里写明原因。行情陈旧、采集任务失败或只跑一半会写进 `warnings`。

    hours: 消息和期权异动往回看几小时，默认 24
    """
    try:
        hours = max(1, min(int(hours), 24 * 7))
        rows = pg.query(_POSITIONS_SQL)
        if not rows:
            return json.dumps({"alerts": [], "checked": 0,
                               "checked_sources": ["price"],
                               "hours": hours, "warnings": [],
                               "data_gaps": [], "alert_count": 0,
                               "returned_count": 0, "truncated": False,
                               "message": "没有持仓，没什么可盯的"},
                              ensure_ascii=False, indent=2)

        by_market: dict[str, list[str]] = {}
        for row in rows:
            by_market.setdefault(row["market"], []).append(row["ticker"])
        cost_of = {r["ticker"]: float(r["avg_cost"] or 0) for r in rows}
        shares_of = {r["ticker"]: float(r["shares"] or 0) for r in rows}
        currency_of = {r["ticker"]: r["currency"] for r in rows}
        all_tickers = [r["ticker"] for r in rows]

        snaps: dict[str, dict | None] = {}
        earnings: dict[str, dict | None] = {}
        lows: dict[str, float] = {}
        gaps: list[str] = []
        warnings: list[str] = []

        # ---- 每个涉及到的市场：行情快照、财报日、阶段新低、新鲜度。
        # 一个市场里某一项挂了，不连累这个市场的其它项，也不连累另一个市场 ----
        for market, tickers in by_market.items():
            try:
                snaps.update(snapshot.get(tickers, market))
            except Exception as exc:
                gaps.append(f"{market} 行情快照拉取失败：{exc}")

            try:
                earnings.update(catalyst.earnings_for(tickers, market))
            except Exception as exc:
                gaps.append(f"{market} 财报日拉取失败：{exc}")

            try:
                for low_row in pg.query(_NEW_LOW_SQL, (tickers, market)):
                    lows[low_row["ticker"]] = float(low_row["low_20"])
            except Exception as exc:
                gaps.append(f"{market} 阶段新低查询失败：{exc}")

            try:
                fresh = regime.freshness(market)
            except Exception as exc:
                fresh = None
                gaps.append(f"{market} 行情新鲜度检测失败：{exc}")
            if fresh:
                warnings.extend(_freshness_warnings(market, fresh))

        alerts: list[dict] = []
        sources = ["price"]

        # ---- 第一路：价格触发。不依赖任何外部接口，只用上面已经拿到的数据 ----
        for ticker, snap in snaps.items():
            if not snap:
                continue
            close = snap["close"]
            currency = currency_of[ticker]
            stop = scoring.position_stop(cost_of[ticker], snap)
            if stop.get("breached"):
                pnl = (close - cost_of[ticker]) * shares_of[ticker]
                # 触发这条不等于亏钱：均线在成本上方时止损本来就抬到了均线上
                # （护浮盈），这时候破位是「浮盈还在，但保护线没了」，不是
                # 「浮亏了」。原来这里不分青红皂白一律印「浮亏 {loss}」，
                # 浮盈时会把正数印成「浮亏 204」——把赚钱的仓位说成在亏钱，
                # 是会让人恐慌割肉的那种误导（2026-08-30 实测：AMZN 浮盈
                # 204 USD 时曾被报成「浮亏 204 USD」，NVO 同理）。
                if pnl >= 0:
                    pnl_text = (f"这笔仓位现在还是浮盈 {pnl:.0f} {currency}，"
                               "但价格已经跌破了用来锁定利润的止损线")
                else:
                    pnl_text = (f"这笔仓位当前浮亏 {abs(pnl):.0f} {currency}，"
                               "已经跌出了当初设的容错范围")
                alerts.append(_alert(
                    ticker, "stop_breached",
                    f"现价 {close} 跌破了建议止损位 {stop['suggested_stop']}"
                    f"（依据：{stop['basis']}）",
                    pnl_text,
                    "按纪律该减仓或清掉。要留就得说清楚新的止损放哪、"
                    "为什么这次不一样"))
            if snap.get("above_ma50") is False:
                alerts.append(_alert(
                    ticker, "below_ma50",
                    f"跌破 50 日均线（{snap['ma_50']}）",
                    "中期趋势转弱了，波段的多头依据没了",
                    "波段仓位考虑离场；要留就当成中线仓，重新定止损"))
            elif snap.get("above_ma20") is False:
                alerts.append(_alert(
                    ticker, "below_ma20",
                    f"跌破 20 日均线（{snap['ma_20']}）",
                    "短期动能转弱，但中期趋势还在",
                    "先减一部分观察，等站回均线再补"))
            low_20 = lows.get(ticker)
            if low_20 is not None and close is not None and close <= low_20:
                alerts.append(_alert(
                    ticker, "new_low",
                    f"创阶段新低（{close}）",
                    "没有买盘接，下跌还在延续",
                    "别在下跌途中摊平成本，先等企稳信号"))

        # ---- 第二路：财报临近。同样只用已经拿到的数据 ----
        for ticker, info in earnings.items():
            days_until = (info or {}).get("days_until")
            if days_until is not None and 0 <= days_until <= EARNINGS_SOON_DAYS:
                alerts.append(_alert(
                    ticker, "earnings_soon",
                    f"{days_until} 天后财报（{info['next_earnings_date']}）",
                    "财报是掷骰子，一晚上可能跳 10% 以上，方向不可预测",
                    "波段仓位建议财报前减到能承受的量，或者干脆先出来"))

        # ---- 第三路：期权异动（美股）/ 主力资金和龙虎榜（A股）----
        us_tickers = by_market.get("US", [])
        if us_tickers:
            try:
                sources.append("options_flow")
                days = max(1, hours // 24 + 1)
                p90 = {r["ticker"]: r["p90"]
                       for r in pg.query(_FLOW_P90_SQL, (us_tickers,))}
                big_by_ticker: dict[str, list[dict]] = {}
                for row in pg.query(_FLOW_RECENT_SQL, (us_tickers, days)):
                    if is_big_flow(row, p90.get(row["ticker"])):
                        big_by_ticker.setdefault(row["ticker"], []).append(row)

                # 按票汇总，不按笔数扇出——见模块 docstring
                for ticker, big_rows in big_by_ticker.items():
                    bullish = [r for r in big_rows if r["sentiment"] == "bullish"]
                    bearish = [r for r in big_rows if r["sentiment"] == "bearish"]
                    bullish_sum = sum(float(r["total_premium"] or 0)
                                      for r in bullish)
                    bearish_sum = sum(float(r["total_premium"] or 0)
                                      for r in bearish)
                    biggest = max(big_rows,
                                 key=lambda r: float(r["total_premium"] or 0))
                    net_bearish = bearish_sum > bullish_sum
                    alerts.append(_alert(
                        ticker,
                        "big_bearish_flow" if net_bearish else "big_bullish_flow",
                        f"近 {days} 天有 {len(big_rows)} 笔大额期权交易：看涨 "
                        f"{len(bullish)} 笔合计 {bullish_sum / 1e4:.0f} 万美元、"
                        f"看跌 {len(bearish)} 笔合计 {bearish_sum / 1e4:.0f} "
                        f"万美元，最大一笔 {float(biggest['total_premium']) / 1e4:.0f} "
                        f"万美元（{biggest['opt_type']}，行权价 {biggest['strike']}，"
                        f"到期 {biggest['expiry']}）",
                        "有大资金在这个方向上集中押注，" +
                        ("跟你的多头仓位是反的" if net_bearish else "跟你的方向一致"),
                        "别只凭这一条动手，配合价格看——"
                        "价格同时跌破均线才是真信号" if net_bearish
                        else "可以作为继续持有的旁证，不构成加仓理由"))
            except Exception as exc:
                gaps.append(f"期权异动拉取失败：{exc}")

        cn_tickers = by_market.get("CN", [])
        if cn_tickers:
            try:
                sources.append("cn_fund_flow")
                for ticker, flow in cn_signals.fund_flow(cn_tickers).items():
                    if not flow:
                        continue
                    net_5d = flow.get("net_5d") or 0
                    if net_5d < -100_000_000:
                        alerts.append(_alert(
                            ticker, "cn_outflow",
                            f"主力 5 日净流出 {abs(net_5d) / 1e8:.2f} 亿",
                            "机构资金在持续出货，散户接盘的行情很难走远",
                            "考虑减仓，至少不要在这个位置加"))
            except Exception as exc:
                gaps.append(f"A股主力资金拉取失败：{exc}")

            try:
                for ticker, records in cn_signals.lhb(cn_tickers, days=5).items():
                    if records:
                        alerts.append(_alert(
                            ticker, "lhb",
                            f"近 5 天上过龙虎榜（{records[0]['reason']}）",
                            "游资在这只票上进出，波动会明显放大",
                            "把止损放宽一点或者减小仓位，别被日内波动扫掉"))
            except Exception as exc:
                gaps.append(f"龙虎榜拉取失败：{exc}")

        # ---- 第四路：突发消息 ----
        try:
            news = catalyst.news_for(all_tickers, days=max(1, hours // 24),
                                     min_significance="High", per_ticker=1)
            if news:
                sources.append("news")
            for ticker, items in news.items():
                for item in items:
                    negative = (item.get("sentiment_label") or "").lower() == "negative"
                    alerts.append(_alert(
                        ticker, "bad_news" if negative else "good_news",
                        f"重大{'负面' if negative else '正面'}消息：{item['title']}",
                        "重大消息通常会改变这只票未来几天的走势方向",
                        "先看股价反应过了没有——已经跳完的消息不用再动作；"
                        "还没反应的才需要决定"))
        except Exception as exc:
            gaps.append(f"消息拉取失败：{exc}")

        # ---- UW 实时警报：解出标的再跟持仓比对，同一只票多条规则合并成一条 ----
        try:
            if uw.available():
                live = uw.alerts(limit=100)
                if live is not None:
                    sources.append("uw_alerts")
                    held = set(all_tickers)
                    rules_by_ticker: dict[str, list[str]] = {}
                    for item in live:
                        underlying = uw.underlying_of(item)
                        if underlying and underlying in held:
                            name = item.get("name") or item.get("noti_type") or "?"
                            rules_by_ticker.setdefault(underlying, [])
                            if name not in rules_by_ticker[underlying]:
                                rules_by_ticker[underlying].append(name)
                    for ticker, names in rules_by_ticker.items():
                        alerts.append(_alert(
                            ticker, "big_bullish_flow",
                            f"实时警报触发：{'、'.join(names)}",
                            "你配置的扫描规则在这只持仓上触发了",
                            "去 Unusual Whales 看这条警报的细节再决定"))
        except Exception as exc:
            gaps.append(f"UW 实时警报拉取失败：{exc}")

        alerts.sort(key=lambda a: (-a["severity"], a["ticker"]))
        alert_count = len(alerts)
        returned = alerts[:MAX_ALERTS]
        truncated = alert_count > len(returned)

        message = ""
        if not returned:
            message = f"{len(rows)} 只持仓，无异常"

        return json.dumps({
            "alerts": returned,
            "alert_count": alert_count,
            "returned_count": len(returned),
            "truncated": truncated,
            "checked": len(rows),
            "checked_sources": sources,
            "hours": hours,
            "warnings": warnings,
            "data_gaps": sorted(set(gaps)),
            "message": message,
        }, ensure_ascii=False, indent=2, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
