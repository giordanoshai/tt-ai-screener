# -*- coding: utf-8 -*-
"""工具 3：个股分析——单独一只票的完整体检。

把所有能拿到的都拿上：日线趋势、小时线买卖点位、基本面（只有中线档展开）、
近两周新闻和情绪、期权资金流向、gamma 墙、财报日、内部人买卖。
A股换成主力资金、龙虎榜、所属板块强弱。

## 小时线并进来了

原来 `get_hourly_setup` 是一个独立的 MCP 工具。做一只票的体检时，
「日线定方向、小时线定点位」本来就是一件事的两半，分成两个工具意味着
用户每次都要连着调两次。所以并进来，`tools/hourly.py` 只留计算逻辑。

**小时线只采活跃池**（持仓 + 自选 + 粗筛命中）。不在池子里的票没有小时线，
这时 `hourly` 是 None 并在 data_gaps 里写明，不是错误。

## 只有一只票，所以走实时

期权流、gamma 墙走实时接口，8~12 秒可以接受。取不到自动退回库里的。

## 每一路取数各自 try/except（评审 C2）

小时线、基本面、新闻、财报、期权流、gamma 墙、内部人、国会交易、A股各路
信号——每一路各自包在 try/except 里，挂了只把这一路写进 `data_gaps`，
其余照常给结论。早期是整个函数一个 try：`gamma_walls` 挂掉能干掉整只票的
体检，而 gamma 墙只覆盖约 40 只大盘股、代码注释自己写着「加分项，缺了
不致命」。唯一挂了就没法继续的是日线快照——连价格都没有就什么都算不出来。

## 分数旁边必须写清楚「依据有多少」（评审 C4）

`analyze_ticker("000001.SZ", horizon="mid")` 曾经返回 **score 82**、
`data_gaps` 5 条：中线满分 100 里，基本面 35 分 + 相对强度 20 分整块被移出
分母（A股没有基本面数据），剩下的 45 分几乎全来自「价格站在均线上方」；
同一次跑，基本面齐全、营收 +20.6%、ROE 53.4% 的 ORCL 只有 **77 分**。
现在返回体里带 `evidence_pct`（这个分用上了百分之多少的评分表），
`core/scoring.py` 那边还会往 `risks` 里塞一条明写的提醒。

而且那条「价位精确不等于这笔值得做」的低置信度提示原来写成
`if plan and (...)`——`plan` 为 None（没识别出形态）时整条提示被跳过，
**分最高、依据最少的那一类结果恰好是唯一拿不到警告的**。现在提示跟
`plan` 存不存在解耦。
"""
import json

from core import catalyst, cn_signals, fundamental, regime, scoring, snapshot
from tools import hourly as hourly_mod

HORIZONS = ("short", "mid", "both")

_CN_SUFFIXES = (".SH", ".SZ", ".BJ")

#: 新闻拉几条。挑「不是大盘综述」的第一条拿去打分（评审 I3）。
NEWS_LIMIT = 8


def _round2(value):
    """价格类数字只保留 2 位小数交出去（同 `tools/watch_positions.py`）。"""
    return None if value is None else round(float(value), 2)


def _freshness_warnings(market: str, fresh: dict) -> list[str]:
    """把 `regime.freshness()` 的结论翻成人话警告。

    评审 I4：这是五个工具里唯一没有 `warnings` 的一个。2026-08-30
    `us_news` 采集失败、5 个任务只跑了一半，其余四个工具都报了，个股体检
    一个字不提——用户会以为这份体检的消息面是完整的。
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


def _infer_market(ticker: str) -> str | None:
    if ticker.endswith(_CN_SUFFIXES):
        return "CN"
    if ticker.isdigit():
        return None   # A股裸代码，分不清哪个交易所
    return "US"


def analyze_ticker(ticker: str, horizon: str = "both") -> str:
    """一只票的完整体检：趋势、点位、消息、资金、基本面。

    日线定方向、小时线定点位，两个周期一起给。中线档才展开基本面——
    短波段不看公司好不好，只看价格怎么走、钱往哪押、有没有雷。

    ticker:  美股直接写 AAPL；A股要带交易所后缀，像 600519.SH、000001.SZ
    horizon: short 只看短波段 / mid 只看中线 / both 两档都给（默认）
    """
    try:
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return json.dumps({"error": "没给股票代码"}, ensure_ascii=False)

        horizon = (horizon or "both").lower()
        if horizon not in HORIZONS:
            return json.dumps({"error": f"horizon 只能是 short / mid / both，"
                                        f"收到 {horizon}"}, ensure_ascii=False)

        market = _infer_market(ticker)
        if market is None:
            return json.dumps({
                "error": f"认不出 {ticker} 属于哪个市场。"
                         "A股要带交易所后缀（600519.SH / 000001.SZ / 430139.BJ）",
            }, ensure_ascii=False)

        gaps: list[str] = []

        # 每一路各自 try/except：挂了只丢这一路，其余照常给结论（评审 C2）
        def _try(label: str, fn, default):
            try:
                return fn()
            except Exception as exc:
                gaps.append(f"{label}拉取失败：{exc}")
                return default

        # 日线快照是唯一挂了就没法继续的一路：连价格都没有，
        # 趋势、形态、计划、评分全都无从算起
        snap = snapshot.get([ticker], market).get(ticker)
        if snap is None:
            return json.dumps({
                "ticker": ticker, "market": market, "trend": None,
                "hourly": None, "fundamental": None, "news": [], "flow": None,
                "gamma": None, "earnings": None, "insider": None,
                "congress": None, "cn": None, "setup": None, "plan": None,
                "error": f"{ticker}（{market}）在库里没有日线数据",
                "warnings": [], "data_gaps": ["没有行情数据"],
            }, ensure_ascii=False, indent=2, default=str)

        if snap["stale"]:
            gaps.append(f"行情已经 {snap['days_since_bar']} 天没更新")

        # ---- 小时线：只有活跃池才有 ----
        hourly = _try("小时线",
                      lambda: hourly_mod.hourly_setup(ticker, bars=60,
                                                      market=market), None)
        if hourly is None or hourly.get("error"):
            gaps.append("这只票没有小时线数据（小时线只采持仓+自选+粗筛命中的票）")
            hourly = None

        # ---- 基本面：只有中线档才展开 ----
        fund = None
        if horizon in ("mid", "both"):
            fund = _try("基本面",
                        lambda: fundamental.get([ticker], market).get(ticker),
                        None)
            if fund is None:
                # 措辞跟 `core/scoring.py` 用同一个常量。差一个字的话
                # `sorted(set(...))` 去不掉重，同一件事会在 data_gaps 里
                # 出现两遍（评审顺手修一条）
                gaps.append(scoring.NO_FUNDAMENTAL_GAP)

        news = _try("新闻",
                    lambda: catalyst.news_for(
                        [ticker], days=14, min_significance="Low",
                        per_ticker=NEWS_LIMIT).get(ticker, []), [])
        # 每条新闻标一下是不是大盘综述——挂了很多只票的那种说的是「今天
        # 大盘怎么样」，不是这只票的专属消息（评审 I3）
        news = [dict(item, market_wide=catalyst.is_market_wide(item))
                for item in news]
        if not news:
            gaps.append("近 14 天没有相关新闻")

        earnings = _try("财报日",
                        lambda: catalyst.earnings_for([ticker],
                                                      market).get(ticker), None)

        flow = gamma = insider = congress = cn = None
        if market == "US":
            flow = _try("期权异动",
                        lambda: catalyst.options_flow_live([ticker]).get(ticker),
                        None)
            if flow and flow.get("source") == "db":
                gaps.append("期权数据取自数据库（实时接口没取到），一天只更新两次")
            # gamma 墙是加分项，只覆盖约 40 只大盘股，缺了不致命——
            # 它更不该有本事把整只票的体检干掉（评审 C2）
            gamma = _try("gamma 墙",
                         lambda: catalyst.gamma_walls([ticker]).get(ticker),
                         None)
            if gamma is None:
                gaps.append("没有 gamma 墙数据（只覆盖约 40 只大盘股）")
            insider = _try("内部人交易",
                           lambda: catalyst.insider_for(
                               [ticker], days=180).get(ticker), None)
            congress = _try("国会交易",
                            lambda: catalyst.congress_for(
                                [ticker], days=365).get(ticker, []), [])
        else:
            # 板块缺失不在这里 append——score_short_cn 在真正用得上板块的时候
            # 自己会写进 gaps。两处都报的话，同一件事会在清单里出现两遍
            board = _try("A股板块",
                         lambda: cn_signals.board_of([ticker]).get(ticker), None)
            cn = {
                "fund_flow": _try(
                    "A股主力资金",
                    lambda: cn_signals.fund_flow([ticker]).get(ticker), None),
                "lhb": _try("龙虎榜",
                            lambda: cn_signals.lhb(
                                [ticker], days=90).get(ticker, []), []),
                "board": board,
                "market_sentiment": _try("A股涨停情绪",
                                         cn_signals.limit_sentiment, None),
            }
            if cn["fund_flow"] is None:
                gaps.append("没有主力资金数据")

            dates = _try("A股信号表日期", cn_signals.signal_dates, None)
            if dates and not dates["in_sync"]:
                gaps.append(dates["gap"])

        # 行情新不新鲜、采集任务有没有挂，跟另外四个工具一样要报（评审 I4）
        fresh = _try(f"{market} 行情新鲜度",
                     lambda: regime.freshness(market), None)
        warnings = _freshness_warnings(market, fresh) if fresh else []

        # ---- 形态和交易计划 ----
        # 打分用的那条新闻要挑「不是大盘综述」的第一条（评审 I3）
        first_news = catalyst.first_stock_specific(news)
        setup, tier = None, "short"
        if horizon != "mid":
            # mid 档明确表示「只看中线」，就不该先拿短线形态去套——
            # 不然 mid 和 both 除了回显的 horizon 字段之外会一模一样
            setup = scoring.detect_short_setup(snap)
        if setup is None and horizon in ("mid", "both"):
            setup = scoring.detect_mid_setup(snap, fund)
            tier = "mid"

        if tier == "short" and market == "US":
            scored = scoring.score_short(snap, flow, first_news)
        elif tier == "short":
            scored = scoring.score_short_cn(
                snap, (cn or {}).get("fund_flow"), (cn or {}).get("board"),
                (cn or {}).get("market_sentiment"), first_news)
        else:
            scored = scoring.score_mid(snap, fund, first_news)

        plan = scoring.plan_trade(snap, setup) if setup else None
        risks = list(scored["risks"])

        # 数缺口时必须用**跟 data_gaps 完全同一个集合**，不能只数 `gaps`。
        #
        # `gaps` 只装工具层的缺口（取数失败、没小时线、没基本面），评分层的
        # 缺口在 `scored["gaps"]` 里，计划层的在 `plan["gaps"]` 里，最后
        # data_gaps 是三者合并。只数第一个有两个后果：
        #   1. 提示里那句「另有 N 项依据缺失」跟用户眼前看到的 data_gaps
        #      对不上——列 5 条却说 1 条。
        #   2. 更糟的是**提示会在数据变好之后失灵**。2026-09-01 真踩到：
        #      早上 000001.SZ 工具层缺三四项（没小时线、没新闻、行情陈旧），
        #      条件碰巧成立；下午 A股数据补齐、小时线改走实时接口之后，
        #      那些缺口消失，`len(gaps)` 掉到 1，于是「82 分但只用上 45%
        #      评分表」这种最该被警告的结果反而不再警告了。
        all_gaps = sorted(set(gaps + scored["gaps"]
                              + (plan["gaps"] if plan else [])))

        # 低置信度提示**不挂在 plan 的存在与否上**（评审 C4）。
        # 原来写的是 `if plan and (...)`：没识别出形态时 plan 是 None，
        # 整条提示被跳过——而「没形态 + 缺一堆依据 + 分还不低」恰恰是最该
        # 被警告的那一类结果，它成了唯一拿不到警告的一类。
        if scored["score"] < 50 or len(all_gaps) >= 3:
            plan_note = ("这份计划的价位是按现价和 ATR 机械算出来的。"
                         if plan else "")
            # 依据比例低到一定程度时，`core/scoring` 已经在 risks 里说过
            # 「只用上了 N% 的评分表」了，这里不再重复同一句话
            evidence_note = (
                "" if scored["evidence_pct"] < scoring.LOW_EVIDENCE_PCT
                else f"（用上了 {scored['evidence_pct']}% 的评分表）")
            risks.append(
                f"{plan_note}评分 {scored['score']} 分{evidence_note}，"
                f"另有 {len(all_gaps)} 项依据缺失"
                + ("——价位精确不等于这笔值得做" if plan
                   else "——依据这么少，这个分本身就该打个问号"))

        return json.dumps({
            "ticker": ticker,
            "market": market,
            "name": snap.get("company_name"),
            "as_of": snap["date"],
            "horizon": horizon,
            "trend": {
                # 一律留 2 位小数交出去：原样透传的话
                # `"rsi": 55.8322681571604` 这种十几位小数直接糊到用户脸上
                "close": _round2(snap["close"]),
                "pct_chg": _round2(snap["pct_chg"]),
                "above_ma20": snap["above_ma20"],
                "above_ma50": snap["above_ma50"],
                "above_ma200": snap["above_ma200"],
                "ma_20": _round2(snap["ma_20"]),
                "ma_50": _round2(snap["ma_50"]),
                "ma_200": _round2(snap["ma_200"]),
                "ma_50_slope_pct": _round2(snap["ma_50_slope_pct"]),
                "dist_ma20_pct": _round2(snap["dist_ma20_pct"]),
                "dist_ma50_pct": _round2(snap["dist_ma50_pct"]),
                "rsi_14": _round2(snap["rsi_14"]),
                "vol_ratio": _round2(snap["vol_ratio"]),
                "atr_14": _round2(snap["atr_14"]),
                "atr_pct": _round2(snap["atr_pct"]),
                "high_20": _round2(snap["high_20"]),
                "high_55": _round2(snap["high_55"]),
                "dist_from_20d_high_pct": _round2(
                    snap["dist_from_20d_high_pct"]),
                "dollar_volume": _round2(snap["dollar_volume"]),
            },
            "hourly": hourly,
            "fundamental": fund,
            "news": news,
            "flow": flow,
            "gamma": gamma,
            "earnings": earnings,
            "insider": insider,
            "congress": congress,
            "cn": cn,
            "setup": setup,
            "setup_label": scoring.SETUP_LABELS.get(setup) if setup else None,
            "score": scored["score"],
            # 这个分是用多少比例的评分表打出来的（100 = 一项没缺）。
            # 45% 的依据打出来的 82 分，跟 100% 的依据打出来的 77 分
            # 不是同一种东西，这个字段就是用来把这件事写在脸上的（评审 C4）
            "evidence_pct": scored["evidence_pct"],
            "reasons": scored["reasons"],
            "risks": risks,
            "plan": plan,
            "warnings": warnings,
            # 就是上面数过的那个集合，别再拼一遍——两处拼法一旦漂移，
            # 提示里说的条数就会跟这张清单对不上。
            "data_gaps": all_gaps,
        }, ensure_ascii=False, indent=2, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
