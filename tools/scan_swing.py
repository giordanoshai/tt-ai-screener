# -*- coding: utf-8 -*-
"""工具 2：波段扫描——盘后找值得做的波段机会。

## 为什么只有盘后，没有盘前

日线和期权异动都是收盘后才灌全的，盘前跑等于拿昨天的数据算今天的结论。
而且持有周期是 3 天到 2 个月，按周算，不需要抢开盘那几分钟。

用法：每天盘后跑 `scope="pool"`（持仓 + 自选，几百只）；
周一和周四盘后跑 `scope="full"`（全市场 2000 多只）。

## 为什么全市场扫描走数据库不走实时接口

2082 只美股要算均线、RSI、ATR、20 日新高。这些指标库里已经预先算好了，
一次查询 0.1 秒。走 UW 实时接口要调 2082 次日线，并发 20 路也要 15 分钟——
在对话里等不到结果，而且 UW 的日线只给原始 OHLC 不给指标，等于要在 Python 里
把整套指标重写一遍。

筛完剩十几只之后，`scope="pool"` 会对最终入选的几只补一次实时期权流。

## 财报排雷的两档不一样

短波段：财报 3 个交易日内的**直接剔除**。波段是赌趋势延续，财报是掷骰子，
两件事不该混在一笔交易里。
中线：**保留但标注**。持有两个月本来就会跨过财报，而且财报可能正是催化剂。

## 期权流有两路，评分用的是哪一路必须标出来（评审 I1）

打分用的是**库里 5 天的期权归档**（`catalyst.options_flow`）；`scope="pool"`
时最后还会给入选的几只补一次 **UW 实时**期权流。早期实现把实时那份直接
覆盖到同一个 `flow` 键上，两者都不带标签——2026-08-31 实测：NVDA 的
reasons 写「期权资金偏空（净 −10%）」，旁边 `flow.net_premium` 却是净
**−43.7%**；S 的 reasons 写「大致平衡」（归档 +1.6%），旁边 `flow` 是
**−15.1%**。评分依据和贴出来的证据是两个不同的东西，读的人会以为算错了。

现在两份分开放、各自标明来源和时间窗：`flow`（打分用的那份，
`used_for_score: true`）和 `flow_live`（后补的实时那份，
`used_for_score: false`）。

## 每一路取数各自 try/except（评审 C2）

基本面、财报、新闻、期权流、A股各路信号、市场环境、新鲜度——每一路各自
包在 try/except 里，挂了只把这一路写进 `data_gaps`，其余照常出候选。
唯一的例外是行情快照本身：连价格都没有的话，这个工具没有任何东西可以
算，那种情况才返回 error。
"""
import json

from core import (catalyst, cn_signals, fundamental, regime, scoring,
                  snapshot)
from db import pg

HORIZONS = ("short", "mid", "both")
SCOPES = ("pool", "full")

#: 一次最多返回多少只。再多 Claude 也讲不完，用户也看不过来。
MAX_LIMIT = 30

#: 短波段排雷：财报在这么多天内的直接剔除。
#:
#: 模块 docstring 声明的是「财报 **3 个交易日**内直接剔除」，而
#: `earnings_for()` 给的 `days_until` 是**自然日**，所以这里要写 5
#: （3 个交易日跨一个周末就是 5 个自然日）。早期这里写的是 3，注释却写着
#: 「3 个交易日 ≈ 5 个自然日」——注释和值对不上，实际排雷窗口比声称的窄
#: 了两天（评审 I2）。`review_positions.EARNINGS_SOON_DAYS` 用的是同一个
#: 5 天窗口、同一套换算；`watch_positions` 用 7 天，因为它是盯盘、职责是
#: 提前打招呼，理由写在那边的常量注释里。
EARNINGS_BLACKOUT_DAYS = 5

#: 每票拉几条新闻候选，再从里面挑第一条「不是大盘综述」的拿去打分。
#: 拉 1 条再判断的话，那 1 条恰好是综述就等于这只票没有消息面依据了
#: （跟 `watch_positions.NEWS_CANDIDATES_PER_TICKER` 是同一个道理）。
NEWS_CANDIDATES_PER_TICKER = 3

#: pool 范围里，最终给几只补实时期权流。全市场扫描不补（太慢）。
LIVE_FLOW_TOP_N = 15

_POOL_SQL = """
select ticker from user_positions where market = %s
union
select w.ticker
from user_watchlist w
left join stocks_meta m on m.ticker = w.ticker
where coalesce(m.market, 'US') = %s
"""


def _pool_tickers(market: str) -> list[str]:
    return [row["ticker"] for row in pg.query(_POOL_SQL, (market, market))]


def _passes_liquidity(snap: dict, market: str) -> bool:
    """pool 范围也要过流动性门槛——自选里可能躺着一只没人交易的票。"""
    floor = snapshot.DEFAULT_MIN_DOLLAR_VOLUME[market]
    return bool(snap.get("dollar_volume") and snap["dollar_volume"] >= floor)


def _freshness_warnings(market: str, fresh: dict) -> list[str]:
    """把 `regime.freshness()` 的结论翻成人话警告。拆成纯函数是为了能测
    ——真实数据哪天行情不陈旧、采集任务都正常，端到端测这条逻辑就会全绿。
    失败和「只跑了一半」无条件报，不挂在 stale 下面：「行情是新的但某个
    采集任务挂了」恰恰最该说。"""
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


def _candidates(snaps: list[dict], horizon: str,
                funds: dict) -> list[tuple[dict, str, str]]:
    """挑出命中形态的票。返回 (快照, 形态, 档位)。"""
    out = []
    for snap in snaps:
        if horizon in ("short", "both"):
            setup = scoring.detect_short_setup(snap)
            if setup:
                out.append((snap, setup, "short"))
                continue
        if horizon in ("mid", "both"):
            setup = scoring.detect_mid_setup(snap, funds.get(snap["ticker"]))
            if setup:
                out.append((snap, setup, "mid"))
    return out


def _round2(value):
    """价格类数字只保留 2 位小数交出去。`"price": 64.3899993896484` 这种
    十几位小数没有任何信息量（同 `tools/watch_positions.py` 的 `_round2`）。"""
    return None if value is None else round(float(value), 2)


def _shape(snap: dict, setup: str, tier: str, scored: dict,
           plan: dict, earnings: dict | None, extra_gaps: list[str],
           flow: dict | None = None) -> dict:
    risks = list(scored["risks"])
    days_until = (earnings or {}).get("days_until")
    if tier == "mid" and days_until is not None and 0 <= days_until <= EARNINGS_BLACKOUT_DAYS:
        risks.append(f"{days_until} 天后财报，可能是催化剂也可能是雷")

    return {
        "ticker": snap["ticker"],
        "name": snap.get("company_name"),
        "market": snap["market"],
        "horizon": tier,
        "setup": setup,
        "setup_label": scoring.SETUP_LABELS.get(setup, setup),
        "price": _round2(snap["close"]),
        "rsi": _round2(snap.get("rsi_14")),
        "vol_ratio": _round2(snap.get("vol_ratio")),
        "atr_pct": _round2(snap.get("atr_pct")),
        "entry_zone": plan["entry_zone"],
        "stop": plan["stop"],
        "stop_pct": plan["stop_pct"],
        "targets": plan["targets"],
        "rr_ratio": plan["rr_ratio"],
        "score": scored["score"],
        # 这个分是用多少比例的评分表打出来的（100 = 一项没缺）。缺一半
        # 依据打出来的 80 分，跟依据齐全打出来的 80 分不是一回事（评审 C4）
        "evidence_pct": scored["evidence_pct"],
        "reasons": scored["reasons"],
        "risks": risks,
        # 打分用的就是这一份（库里 5 天的期权归档）。实时那份在
        # `flow_live` 里另放，两份都标了来源和时间窗（评审 I1）
        "flow": (dict(flow, window="库里近 5 天的期权归档",
                      used_for_score=True) if flow else None),
        "earnings": earnings or {},
        "data_gaps": sorted(set(scored["gaps"] + plan["gaps"] + extra_gaps)),
    }


def scan_swing(market: str = "US", horizon: str = "short",
               scope: str = "pool", limit: int = 15) -> str:
    """盘后扫一遍，找值得做的波段机会。

    每只入选的票都给出：为什么是现在（形态和理由）、买在哪（区间）、
    错了在哪认输（按 ATR 算的止损，不是拍脑袋的百分比）、对了到哪走
    （2 倍和 3 倍风险距离两档）。

    用法：每天盘后跑 scope="pool"；周一、周四盘后跑 scope="full"。
    不做盘前扫描——日线和期权数据收盘后才全，而且持有周期按周算。

    market:  US 美股 / CN A股
    horizon: short 短波段（3 天~2 周）/ mid 中线（2 周~2 月）/ both 两档都要
    scope:   pool 只看持仓和自选 / full 全市场
    limit:   最多返回几只，默认 15，上限 30
    """
    try:
        market = (market or "US").upper()
        horizon = (horizon or "short").lower()
        scope = (scope or "pool").lower()
        if market not in snapshot.MARKETS:
            return json.dumps({"error": f"market 只能是 US 或 CN，收到 {market}"},
                              ensure_ascii=False)
        if horizon not in HORIZONS:
            return json.dumps({"error": f"horizon 只能是 short / mid / both，"
                                        f"收到 {horizon}"}, ensure_ascii=False)
        if scope not in SCOPES:
            return json.dumps({"error": f"scope 只能是 pool 或 full，收到 {scope}"},
                              ensure_ascii=False)
        limit = max(1, min(int(limit), MAX_LIMIT))

        gaps: list[str] = []

        # 每一路各自 try/except：挂了只丢这一路，其余照常出候选（评审 C2）
        def _try(label: str, fn, default):
            try:
                return fn()
            except Exception as exc:
                gaps.append(f"{label}拉取失败：{exc}")
                return default

        # ---- 1. 取候选池。这一路是唯一挂了就没法继续的：连价格都没有，
        # 后面所有判断都无从谈起 ----
        if scope == "full":
            snaps = snapshot.scan(market)
        else:
            tickers = _pool_tickers(market)
            snaps = [s for s in snapshot.get(tickers, market).values() if s]
            snaps = [s for s in snaps if _passes_liquidity(s, market)]
        scanned = len(snaps)

        # ---- 2. 中线档要基本面才能判形态 ----
        funds = {}
        if horizon in ("mid", "both"):
            funds = _try("基本面",
                         lambda: fundamental.get(
                             [s["ticker"] for s in snaps], market), {})
            if market == "CN":
                gaps.append("A股基本面只覆盖 150 只票且字段大多为空，"
                            "中线档的判断依据不完整")

        # ---- 3. 命中形态的才进下一步 ----
        hits = _candidates(snaps, horizon, funds)
        codes = [snap["ticker"] for snap, _, _ in hits]

        # ---- 4. 只对命中的票补消息面和资金面 ----
        earnings = _try("财报日",
                        lambda: catalyst.earnings_for(codes, market), {})
        news = _try("新闻",
                    lambda: catalyst.news_for(
                        codes, days=7, min_significance="Medium",
                        per_ticker=NEWS_CANDIDATES_PER_TICKER), {})

        flows, cn_flows, boards, sentiment = {}, {}, {}, None
        if market == "US":
            flows = _try("期权异动",
                         lambda: catalyst.options_flow(codes, days=5), {})
        else:
            cn_flows = _try("A股主力资金",
                            lambda: cn_signals.fund_flow(codes), {})
            # 板块和情绪只有短线档用得上——score_mid 连 board 参数都没有。
            # 无条件报「缺板块数据」会把一个中线档根本不用的缺口说成问题
            if horizon in ("short", "both"):
                boards = _try("A股板块", lambda: cn_signals.board_of(codes), {})
                sentiment = _try("A股涨停情绪",
                                 cn_signals.limit_sentiment, None)
                gaps.append(cn_signals.BOARD_MAPPING_GAP)
                dates = _try("A股信号表日期", cn_signals.signal_dates, None)
                if dates and not dates["in_sync"]:
                    gaps.append(dates["gap"])

        # ---- 5. 短波段排雷：财报窗口内的直接剔除 ----
        rows = []
        for snap, setup, tier in hits:
            code = snap["ticker"]
            days_until = (earnings.get(code) or {}).get("days_until")
            if (tier == "short" and days_until is not None
                    and 0 <= days_until <= EARNINGS_BLACKOUT_DAYS):
                continue

            # 打分用的那条新闻要挑「不是大盘综述」的第一条（评审 I3）。
            # 真实后果：NVDA 靠一条挂了 7 只票的《马斯克加速燃气轮机生产
            # 之路伴随污染难题》拿到消息面满分 20/20——那是大盘/行业综述，
            # 不是这只票的催化剂
            first_news = catalyst.first_stock_specific(news.get(code))
            if tier == "short" and market == "US":
                scored = scoring.score_short(snap, flows.get(code), first_news)
            elif tier == "short":
                scored = scoring.score_short_cn(
                    snap, cn_flows.get(code), boards.get(code),
                    sentiment, first_news)
            else:
                scored = scoring.score_mid(snap, funds.get(code), first_news)

            rows.append(_shape(snap, setup, tier, scored,
                               scoring.plan_trade(snap, setup),
                               earnings.get(code), [], flows.get(code)))

        # 同分时先看依据足不足，再看量能。
        #
        # 为什么依据要进排序键：`horizon="both"` 会把中线档和短线档放进
        # **同一个列表**，而中线满分里基本面占 35 分、相对强度占 20 分，
        # A股这两块经常整块缺失（实测 000001.SZ 中线 evidence_pct=45）。
        # 只按分数排的话，一个用 45% 评分表打出来的 82 分会排在一个用完整
        # 评分表打出来的 77 分前面——那两个 82 不是一个东西。
        #
        # 这里**不给分数打折**（打折等于把「缺数据」重新变成扣分，会让所有
        # 现有阈值悄悄变味），只在**同分时**让依据足的排前面。不改变任何
        # 阈值的含义，只决定平局。
        rows.sort(key=lambda r: (-r["score"], -(r.get("evidence_pct") or 0),
                                 -(r.get("vol_ratio") or 0), r["ticker"]))
        matched_count = len(rows)
        rows = rows[:limit]

        # ---- 6. pool 范围给最终入选的补一次实时期权流。**另放一个键**，
        # 不覆盖打分用的那份——覆盖的话 reasons 里的百分比会跟旁边贴着的
        # 证据对不上（评审 I1）----
        if scope == "pool" and market == "US" and rows:
            live = _try("实时期权流",
                        lambda: catalyst.options_flow_live(
                            [r["ticker"] for r in rows[:LIVE_FLOW_TOP_N]]), {})
            for row in rows:
                flow = live.get(row["ticker"])
                if flow:
                    row["flow_live"] = dict(
                        flow, window="实时接口（取不到时退回库里的，看 source）",
                        used_for_score=False)

        fresh = _try(f"{market} 行情新鲜度",
                     lambda: regime.freshness(market), None)
        warnings = _freshness_warnings(market, fresh) if fresh else []

        return json.dumps({
            "market": market,
            "horizon": horizon,
            "scope": scope,
            # 停牌退市的票最后一根 K 线不是最新交易日，取 max 而不是取第一只
            # ——`snapshot.get()` 刻意保留这类票，任何一只排在首位就会给整批
            # 结果贴错日期
            "as_of": max((s["date"] for s in snaps if s.get("date")),
                         default=None),
            "scanned": scanned,
            # matched = 命中形态的只数（财报排雷**之前**）；
            # qualified = 排完雷、真正够格进候选名单的只数；
            # returned_count = 被 limit 砍完实际返回的只数。
            # 三个数基数不同，分开写出来，别让人拿 matched 去跟
            # returned_count 相减以为那就是被截断的量
            "matched": len(hits),
            "qualified": matched_count,
            "candidates": rows,
            "returned_count": len(rows),
            "truncated": matched_count > len(rows),
            "regime": _try(f"{market} 市场环境",
                           lambda: regime.get(market), None),
            "warnings": warnings,
            "data_gaps": sorted(set(gaps)),
        }, ensure_ascii=False, indent=2, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
