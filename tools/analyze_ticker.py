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
"""
import json

from core import catalyst, cn_signals, fundamental, scoring, snapshot
from tools import hourly as hourly_mod

HORIZONS = ("short", "mid", "both")

_CN_SUFFIXES = (".SH", ".SZ", ".BJ")


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
        snap = snapshot.get([ticker], market).get(ticker)
        if snap is None:
            return json.dumps({
                "ticker": ticker, "market": market, "trend": None,
                "hourly": None, "fundamental": None, "news": [], "flow": None,
                "gamma": None, "earnings": None, "insider": None,
                "congress": None, "cn": None, "setup": None, "plan": None,
                "error": f"{ticker}（{market}）在库里没有日线数据",
                "data_gaps": ["没有行情数据"],
            }, ensure_ascii=False, indent=2, default=str)

        if snap["stale"]:
            gaps.append(f"行情已经 {snap['days_since_bar']} 天没更新")

        # ---- 小时线：只有活跃池才有 ----
        hourly = hourly_mod.hourly_setup(ticker, bars=60, market=market)
        if hourly.get("error"):
            gaps.append("这只票没有小时线数据（小时线只采持仓+自选+粗筛命中的票）")
            hourly = None

        # ---- 基本面：只有中线档才展开 ----
        fund = None
        if horizon in ("mid", "both"):
            fund = fundamental.get([ticker], market).get(ticker)
            if fund is None:
                gaps.append("没有基本面数据"
                            + ("（A股基本面只覆盖 150 只票）" if market == "CN" else ""))

        news = catalyst.news_for([ticker], days=14, min_significance="Low",
                                 per_ticker=8).get(ticker, [])
        if not news:
            gaps.append("近 14 天没有相关新闻")

        earnings = catalyst.earnings_for([ticker], market).get(ticker)

        flow = gamma = insider = congress = cn = None
        if market == "US":
            flow = catalyst.options_flow_live([ticker]).get(ticker)
            if flow and flow.get("source") == "db":
                gaps.append("期权数据取自数据库（实时接口没取到），一天只更新两次")
            gamma = catalyst.gamma_walls([ticker]).get(ticker)
            if gamma is None:
                gaps.append("没有 gamma 墙数据（只覆盖约 40 只大盘股）")
            insider = catalyst.insider_for([ticker], days=180).get(ticker)
            congress = catalyst.congress_for([ticker], days=365).get(ticker, [])
        else:
            board = cn_signals.board_of([ticker]).get(ticker)
            if board is None:
                gaps.append(cn_signals.BOARD_MAPPING_GAP)
            cn = {
                "fund_flow": cn_signals.fund_flow([ticker]).get(ticker),
                "lhb": cn_signals.lhb([ticker], days=90).get(ticker, []),
                "board": board,
                "market_sentiment": cn_signals.limit_sentiment(),
            }
            if cn["fund_flow"] is None:
                gaps.append("没有主力资金数据")

            dates = cn_signals.signal_dates()
            if not dates["in_sync"]:
                gaps.append(dates["gap"])

        # ---- 形态和交易计划 ----
        first_news = news[0] if news else None
        setup = scoring.detect_short_setup(snap)
        tier = "short"
        if setup is None and horizon in ("mid", "both"):
            setup = scoring.detect_mid_setup(snap, fund)
            tier = "mid" if setup else "short"

        if tier == "short" and market == "US":
            scored = scoring.score_short(snap, flow, first_news)
        elif tier == "short":
            scored = scoring.score_short_cn(
                snap, (cn or {}).get("fund_flow"), (cn or {}).get("board"),
                (cn or {}).get("market_sentiment"), first_news)
        else:
            scored = scoring.score_mid(snap, fund, first_news)

        plan = scoring.plan_trade(snap, setup) if setup else None

        return json.dumps({
            "ticker": ticker,
            "market": market,
            "name": snap.get("company_name"),
            "as_of": snap["date"],
            "horizon": horizon,
            "trend": {
                "close": snap["close"],
                "pct_chg": snap["pct_chg"],
                "above_ma20": snap["above_ma20"],
                "above_ma50": snap["above_ma50"],
                "above_ma200": snap["above_ma200"],
                "ma_20": snap["ma_20"], "ma_50": snap["ma_50"],
                "ma_200": snap["ma_200"],
                "ma_50_slope_pct": snap["ma_50_slope_pct"],
                "rsi_14": snap["rsi_14"],
                "vol_ratio": snap["vol_ratio"],
                "atr_14": snap["atr_14"], "atr_pct": snap["atr_pct"],
                "high_20": snap["high_20"], "high_55": snap["high_55"],
                "dist_from_20d_high_pct": snap["dist_from_20d_high_pct"],
                "dollar_volume": snap["dollar_volume"],
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
            "reasons": scored["reasons"],
            "risks": scored["risks"],
            "plan": plan,
            "data_gaps": sorted(set(gaps + scored["gaps"]
                                    + (plan["gaps"] if plan else []))),
        }, ensure_ascii=False, indent=2, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
