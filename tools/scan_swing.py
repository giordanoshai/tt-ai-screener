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
"""
import json

from core import (catalyst, cn_signals, fundamental, regime, scoring,
                  snapshot)
from db import pg

HORIZONS = ("short", "mid", "both")
SCOPES = ("pool", "full")

#: 一次最多返回多少只。再多 Claude 也讲不完，用户也看不过来。
MAX_LIMIT = 30

#: 短波段排雷：财报在这么多天内的直接剔除（3 个交易日 ≈ 5 个自然日）。
EARNINGS_BLACKOUT_DAYS = 3

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


def _shape(snap: dict, setup: str, tier: str, scored: dict,
           plan: dict, earnings: dict | None, extra_gaps: list[str]) -> dict:
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
        "price": snap["close"],
        "entry_zone": plan["entry_zone"],
        "stop": plan["stop"],
        "stop_pct": plan["stop_pct"],
        "targets": plan["targets"],
        "rr_ratio": plan["rr_ratio"],
        "score": scored["score"],
        "reasons": scored["reasons"],
        "risks": risks,
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

        # ---- 1. 取候选池 ----
        if scope == "full":
            snaps = snapshot.scan(market)
        else:
            tickers = _pool_tickers(market)
            snaps = [s for s in snapshot.get(tickers, market).values() if s]
            snaps = [s for s in snaps if _passes_liquidity(s, market)]
        scanned = len(snaps)

        gaps: list[str] = []

        # ---- 2. 中线档要基本面才能判形态 ----
        funds = {}
        if horizon in ("mid", "both"):
            funds = fundamental.get([s["ticker"] for s in snaps], market)
            if market == "CN":
                gaps.append("A股基本面只覆盖 150 只票且字段大多为空，"
                            "中线档的判断依据不完整")

        # ---- 3. 命中形态的才进下一步 ----
        hits = _candidates(snaps, horizon, funds)
        codes = [snap["ticker"] for snap, _, _ in hits]

        # ---- 4. 只对命中的票补消息面和资金面 ----
        earnings = catalyst.earnings_for(codes, market)
        news = catalyst.news_for(codes, days=7, min_significance="Medium",
                                 per_ticker=1)

        flows, cn_flows, boards, sentiment = {}, {}, {}, None
        if market == "US":
            flows = catalyst.options_flow(codes, days=5)
        else:
            cn_flows = cn_signals.fund_flow(codes)
            boards = cn_signals.board_of(codes)
            sentiment = cn_signals.limit_sentiment()
            gaps.append(cn_signals.BOARD_MAPPING_GAP)
            dates = cn_signals.signal_dates()
            if not dates["in_sync"]:
                gaps.append(dates["gap"])

        # ---- 5. 短波段排雷：财报 3 天内的直接剔除 ----
        rows = []
        for snap, setup, tier in hits:
            code = snap["ticker"]
            days_until = (earnings.get(code) or {}).get("days_until")
            if (tier == "short" and days_until is not None
                    and 0 <= days_until <= EARNINGS_BLACKOUT_DAYS):
                continue

            first_news = (news.get(code) or [None])[0]
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
                               earnings.get(code), []))

        rows.sort(key=lambda r: (-r["score"], r["ticker"]))
        rows = rows[:limit]

        # ---- 6. pool 范围给最终入选的补一次实时期权流 ----
        if scope == "pool" and market == "US" and rows:
            live = catalyst.options_flow_live(
                [r["ticker"] for r in rows[:LIVE_FLOW_TOP_N]])
            for row in rows:
                flow = live.get(row["ticker"])
                if flow:
                    row["flow"] = flow

        fresh = regime.freshness(market)
        warnings = []
        if fresh["stale"]:
            warnings.append(f"{market} 行情最新只到 {fresh['latest_trading_day']}，"
                            f"距今 {fresh['days_since']} 天")
        if fresh["failed_jobs"]:
            # 失败要无条件报。挂在 stale 下面的话，「行情是新的但某个采集任务挂了」
            # 会完全不出声——而那恰恰最该说
            warnings.append(f"{market} 有采集任务失败：{fresh['failed_jobs']}")
        if fresh["partial_jobs"]:
            warnings.append(f"{market} 有采集任务只跑了一半（数据写了，某个可选源没取到）："
                            f"{fresh['partial_jobs']}")

        return json.dumps({
            "market": market,
            "horizon": horizon,
            "scope": scope,
            "as_of": snaps[0]["date"] if snaps else None,
            "scanned": scanned,
            "matched": len(hits),
            "candidates": rows,
            "regime": regime.get(market),
            "warnings": warnings,
            "data_gaps": sorted(set(gaps)),
        }, ensure_ascii=False, indent=2, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
