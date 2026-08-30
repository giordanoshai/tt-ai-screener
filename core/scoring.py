# -*- coding: utf-8 -*-
"""形态识别、分档评分、止损目标价。全是纯函数，不碰数据库也不碰网络。

## 为什么单独一个模块

两个原因。一是这些判断是这套东西里最该被测的部分——阈值定得对不对、
缺数据时会不会算歪，都要能离线反复试。二是**短线和中线的分界要在函数签名上
就锁死**：`score_short()` 的入参里根本没有基本面这个位置，
想在短波段里用市盈率会直接 TypeError，而不是悄悄算进去。

## 归一化：缺数据不等于扣分

某一路数据缺了（A股没有期权数据、停牌票拿不到均线），把那一项从分母里去掉再算
百分比，并把缺的写进 `gaps`。60 分永远表示「拿到的依据里有六成是好的」。
要是缺一项就白扣 25 分，一只好票会因为「我们没查到它的期权数据」
而被排到烂票后面——那是拿系统的无知去惩罚股票。
"""

# ---- 形态阈值。设计文档第四节，改这里等于改策略，别在调用处硬编码 ----

#: 突破要放量到平时的几倍
BREAKOUT_VOL_RATIO = 1.5
#: 回踩：收盘落在 20 日均线的这个区间里（百分比）
PULLBACK_BAND = (-3.0, 2.0)
#: 回踩：RSI 要在这个不温不火的区间
PULLBACK_RSI = (40.0, 60.0)
#: 超跌反弹：RSI 低于这个数
OVERSOLD_RSI = 35.0
#: 超跌反弹：离 50 日均线至少这么远（负数=在均线下方）
OVERSOLD_DIST_MA50 = -8.0
#: 中线趋势延续：离 52 周高点不超过这么多
MID_NEAR_52W_HIGH = 15.0

#: 止损放在入场价下方几倍 ATR。不同波动性的票用同一个百分比止损没有意义。
STOP_ATR_MULT = 1.5
#: 持仓止损：成本下方几倍 ATR（比新开仓宽，已经有浮盈浮亏的仓位不该被扫）
POSITION_STOP_ATR_MULT = 2.0
#: 目标价按几倍风险距离给
TARGET_R_MULTIPLES = (2.0, 3.0)

SHORT_SETUPS = ("breakout", "pullback", "oversold_bounce")
MID_SETUPS = ("trend_continuation", "bottom_reversal")

SETUP_LABELS = {
    "breakout": "突破",
    "pullback": "回踩均线",
    "oversold_bounce": "超跌反弹",
    "trend_continuation": "趋势延续",
    "bottom_reversal": "底部反转",
}


def _num(value):
    """拿一个数，拿不到就返回 None。快照里到处可能是 None。"""
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------- 形态识别


def detect_short_setup(snap: dict) -> str | None:
    """短波段三选一。都不像就返回 None（大多数票都该返回 None）。"""
    close = _num(snap.get("close"))
    ma_20 = _num(snap.get("ma_20"))
    ma_50 = _num(snap.get("ma_50"))
    ma_200 = _num(snap.get("ma_200"))
    rsi = _num(snap.get("rsi_14"))
    vol_ratio = _num(snap.get("vol_ratio"))
    prior_high = _num(snap.get("prior_high_20"))
    dist_ma20 = _num(snap.get("dist_ma20_pct"))
    dist_ma50 = _num(snap.get("dist_ma50_pct"))

    if close is None or ma_20 is None or ma_50 is None:
        return None

    # 突破：收盘越过昨天的 20 日高点 + 放量 + 均线多头排列。
    # 跟「昨天的」高点比是关键——跟今天的比永远不成立，今天的高点包含今天。
    if (prior_high is not None and close > prior_high
            and vol_ratio is not None and vol_ratio >= BREAKOUT_VOL_RATIO
            and close > ma_20 > ma_50):
        return "breakout"

    # 回踩：上升趋势里回到 20 日均线附近喘口气，RSI 不温不火，
    # 而且近 20 日内确实创过新高（不是一路下跌路过均线）
    if (ma_20 > ma_50 and rsi is not None and dist_ma20 is not None
            and PULLBACK_RSI[0] <= rsi <= PULLBACK_RSI[1]
            and PULLBACK_BAND[0] <= dist_ma20 <= PULLBACK_BAND[1]
            and snap.get("made_20d_high_recently")):
        return "pullback"

    # 超跌反弹：长期趋势还在（站上 200 日均线），但短期跌过头了，
    # 而且近 3 日已经出现过放量阳线——有人开始接了，不是空中接飞刀
    if (ma_200 is not None and close > ma_200
            and rsi is not None and rsi < OVERSOLD_RSI
            and dist_ma50 is not None and dist_ma50 < OVERSOLD_DIST_MA50
            and snap.get("had_vol_up_3d")):
        return "oversold_bounce"

    return None


def detect_mid_setup(snap: dict, fund: dict | None) -> str | None:
    """中线两选一。**没有基本面就不给形态**——中线的一半依据在基本面上，
    缺了还硬给一个形态，等于把短线形态贴个中线标签卖出去。"""
    if not fund:
        return None

    close = _num(snap.get("close"))
    ma_50 = _num(snap.get("ma_50"))
    ma_200 = _num(snap.get("ma_200"))
    slope = _num(snap.get("ma_50_slope_pct"))
    if close is None or ma_200 is None:
        return None

    revenue_growth = _num(fund.get("revenue_growth_yoy"))
    profit_margin = _num(fund.get("profit_margin"))
    week_52_high = _num(fund.get("week_52_high"))

    # 趋势延续：多头排列 + 50 日均线在往上 + 离 52 周高点不远 + 生意在赚钱
    if (ma_50 is not None and close > ma_50 > ma_200
            and (slope is None or slope > 0)
            and revenue_growth is not None and revenue_growth > 0
            and profit_margin is not None and profit_margin > 0):
        if week_52_high:
            gap = (week_52_high - close) / week_52_high * 100
            if gap > MID_NEAR_52W_HIGH:
                return None
        return "trend_continuation"

    # 底部反转：刚站上 200 日均线，均线走平或翘头，基本面没在恶化
    if (close > ma_200 and (slope is None or slope >= -0.5)
            and ma_50 is not None and close > ma_50
            and (revenue_growth is None or revenue_growth > -10)):
        return "bottom_reversal"

    return None


# --------------------------------------------------------- 评分


class _Ledger:
    """记分板：每一项记「拿了多少 / 满分多少」，缺的项不进分母。"""

    def __init__(self):
        self.earned = 0.0
        self.possible = 0.0
        self.reasons: list[str] = []
        self.risks: list[str] = []
        self.gaps: list[str] = []

    def add(self, earned: float, possible: float, reason: str | None = None):
        self.earned += earned
        self.possible += possible
        if reason:
            self.reasons.append(reason)

    def skip(self, what: str):
        """这一项没数据。不进分母，写进 gaps。"""
        self.gaps.append(what)

    def risk(self, text: str):
        self.risks.append(text)

    def result(self) -> dict:
        score = round(self.earned / self.possible * 100) if self.possible else 0
        return {
            "score": max(0, min(100, score)),
            "reasons": self.reasons,
            "risks": self.risks,
            "gaps": self.gaps,
        }


def _score_trend(led: _Ledger, snap: dict, weight: float):
    """趋势：站在哪几条均线上方、排列顺不顺。"""
    ma_20, ma_50 = _num(snap.get("ma_20")), _num(snap.get("ma_50"))
    ma_200 = _num(snap.get("ma_200"))
    if ma_20 is None and ma_50 is None and ma_200 is None:
        # 三条均线全缺 = 不知道趋势，不是趋势向下。硬给一条「均线全在头顶」
        # 是在编造证据，比不给结论危险得多。
        led.skip("没有均线数据，趋势这一项算不了")
        return

    got = 0.0
    parts = []
    if snap.get("above_ma20"):
        got += 0.34
        parts.append("站上 20 日均线")
    if snap.get("above_ma50"):
        got += 0.33
        parts.append("站上 50 日均线")
    if ma_20 and ma_50 and ma_20 > ma_50:
        got += 0.16
        parts.append("均线多头排列")
    if snap.get("above_ma200"):
        got += 0.17
        parts.append("站上 200 日均线")
    led.add(got * weight, weight, "、".join(parts) if parts else None)
    if not parts:
        led.risk("均线全在头顶，趋势是向下的")


def _score_volume(led: _Ledger, snap: dict, weight: float):
    """量能：突破有没有量确认。"""
    ratio = _num(snap.get("vol_ratio"))
    if ratio is None:
        led.skip("量比数据缺失")
        return
    if ratio >= 2.0:
        led.add(weight, weight, f"放量 {ratio:.1f} 倍")
    elif ratio >= BREAKOUT_VOL_RATIO:
        led.add(weight * 0.8, weight, f"放量 {ratio:.1f} 倍")
    elif ratio >= 1.2:
        led.add(weight * 0.5, weight, f"量能温和放大（{ratio:.1f} 倍）")
    elif ratio >= 0.8:
        led.add(weight * 0.25, weight, f"量能平常（{ratio:.1f} 倍）")
    else:
        led.add(0.0, weight)
        led.risk(f"缩量到平时的 {ratio:.1f} 倍，没人跟")


def _score_flow(led: _Ledger, flow: dict | None, weight: float):
    """资金确认：期权市场往哪押。"""
    if not flow or not flow.get("alert_count"):
        led.skip("没有期权异动数据")
        return
    bullish = _num(flow.get("bullish_premium")) or 0.0
    bearish = _num(flow.get("bearish_premium")) or 0.0
    total = bullish + bearish
    if total <= 0:
        led.skip("期权异动金额为零")
        return
    tilt = (bullish - bearish) / total
    if tilt >= 0.6:
        led.add(weight, weight, f"期权资金明显偏多（净 {tilt:.0%}）")
    elif tilt >= 0.3:
        led.add(weight * 0.7, weight, f"期权资金偏多（净 {tilt:.0%}）")
    elif tilt >= 0:
        led.add(weight * 0.4, weight, "期权资金多空大致平衡")
    else:
        led.add(0.0, weight)
        led.risk(f"期权资金偏空（净 {tilt:.0%}）")


def _score_news(led: _Ledger, news: dict | None, weight: float):
    """消息面：有没有雷，有没有催化。

    news 是 None **不**当缺数据处理。新闻表一直查得到，空结果就是「近期没有
    够级别的消息」——这本身是个信号（没有雷），跟「期权接口没取到」不是一回事。
    所以给中性偏正的分，不 skip。
    """
    if not news:
        led.add(weight * 0.6, weight, "近期无重大消息")
        return
    label = (news.get("sentiment_label") or "").lower()
    significance = (news.get("significant") or "").lower()
    if label == "negative":
        if significance == "high":
            led.add(0.0, weight)
            led.risk("有重大负面消息")
        else:
            led.add(weight * 0.25, weight)
            led.risk("有负面消息")
    elif label == "positive":
        led.add(weight if significance == "high" else weight * 0.8, weight,
                "有正面消息" + ("（重大）" if significance == "high" else ""))
    else:
        led.add(weight * 0.6, weight, "消息面中性")


def score_short(snap: dict, flow: dict | None = None,
                news: dict | None = None) -> dict:
    """短波段评分：趋势 30 + 量能 25 + 资金确认 25 + 消息面 20。

    **入参里没有基本面，这是故意的。** 短波段不看公司好不好
    （设计文档第三节）。想加市盈率进来会直接 TypeError。
    """
    led = _Ledger()
    _score_trend(led, snap, 30)
    _score_volume(led, snap, 25)
    _score_flow(led, flow, 25)
    _score_news(led, news, 20)
    if snap.get("stale"):
        led.risk(f"行情已经 {snap.get('days_since_bar')} 天没更新")
    return led.result()


def score_short_cn(snap: dict, fund_flow: dict | None = None,
                   board: dict | None = None, sentiment: dict | None = None,
                   news: dict | None = None) -> dict:
    """A股短波段评分：趋势 25 + 主力资金 30 + 板块强度 25 + 情绪与消息 20。

    A股不是「美股那套的残缺版」。它没有个股期权，但有龙虎榜、主力资金明细和
    强烈的板块轮动。主力资金在这里扮演美股的期权异动——确认突破是真是假。
    """
    led = _Ledger()
    _score_trend(led, snap, 25)

    # 主力资金：不能只看正负。0.06 亿和 2.25 亿都算"流入"，但对一只成交 4 亿
    # 和一只成交 24 亿的票，意义完全不同。占比才是 A股这边真正有区分度的量，
    # 相当于美股那边看期权净额的偏向度而不是只看正负。
    if fund_flow is None:
        led.skip("没有主力资金数据")
    else:
        net = _num(fund_flow.get("net_amount")) or 0.0
        net_5d = _num(fund_flow.get("net_5d")) or 0.0
        amount = _num(fund_flow.get("amount")) or 0.0
        ratio = net / amount if amount > 0 else None

        if net <= 0:
            led.add(0, 30)
            led.risk(f"主力资金在流出（当日 {net / 1e8:.2f} 亿）")
        elif ratio is None:
            led.add(15, 30,
                    f"主力当日净流入 {net / 1e8:.2f} 亿（缺成交额，算不出占比）")
        else:
            if ratio >= 0.05:
                base, word = 30, "很强"
            elif ratio >= 0.02:
                base, word = 24, "较强"
            elif ratio >= 0.005:
                base, word = 16, "一般"
            else:
                base, word = 8, "偏弱"
            if net_5d <= 0:
                # 今天流入但 5 日累计还是流出，说明只是反弹不是持续买入
                base = round(base * 0.6)
                led.risk("当日净流入但 5 日累计仍是净流出，持续性存疑")
            led.add(base, 30,
                    f"主力净流入 {net / 1e8:.2f} 亿，占成交额 {ratio:.1%}（{word}），"
                    f"5 日累计 {net_5d / 1e8:.2f} 亿")

    # 板块强度。**A股缺个股→行业映射**（cn_universe.industry 和
    # stocks_meta.industry 对 A股全是空的），大多数票这一项拿不到，
    # 只能明写缺失。见计划开头「已核实的数据事实」。
    if board is None:
        led.skip("缺个股所属板块，板块强度没法算（A股没有行业映射数据）")
    else:
        rank = board.get("rank")
        total = board.get("total_boards") or 1
        if rank is not None and rank <= total * 0.2:
            led.add(25, 25, f"所属板块「{board.get('board_name')}」排在前 20%")
        elif rank is not None and rank <= total * 0.5:
            led.add(15, 25, f"所属板块「{board.get('board_name')}」中上")
        else:
            led.add(5, 25)
            led.risk(f"所属板块「{board.get('board_name')}」偏弱，逆着板块难做")

    # 情绪与消息：涨停家数和炸板率决定今天该不该出手
    if sentiment is None and news is None:
        led.skip("没有市场情绪和消息数据")
    else:
        got = 0.0
        parts = []
        if sentiment:
            limit_up = sentiment.get("limit_up_count") or 0
            broken = _num(sentiment.get("broken_rate"))
            if limit_up >= 60 and (broken is None or broken < 0.3):
                got += 12
                parts.append(f"涨停 {limit_up} 家，情绪偏热")
            elif limit_up >= 30:
                got += 8
                parts.append(f"涨停 {limit_up} 家，情绪一般")
            else:
                parts.append(f"涨停只有 {limit_up} 家，情绪偏冷")
            if broken is not None and broken >= 0.4:
                led.risk(f"炸板率 {broken:.0%}，追涨容易被埋")
        if news:
            label = (news.get("sentiment_label") or "").lower()
            if label == "negative":
                led.risk("有负面消息")
            elif label == "positive":
                got += 8
                parts.append("有正面消息")
            else:
                got += 5
        led.add(got, 20, "、".join(parts) if parts else None)

    if snap.get("stale"):
        led.risk(f"行情已经 {snap.get('days_since_bar')} 天没更新")
    return led.result()


def score_mid(snap: dict, fund: dict | None = None,
              news: dict | None = None) -> dict:
    """中线评分：趋势 25 + 基本面 35 + 相对强度 20 + 消息面 20。

    中线才开始看公司好不好。这个函数**有** fund 参数，短线那两个没有。
    """
    led = _Ledger()
    _score_trend(led, snap, 25)

    if not fund:
        led.skip("没有基本面数据（A股基本面目前只覆盖 150 只票）")
    else:
        # 每一项都按幅度分档，不是「大于零就满分」。净利率 0.1% 的公司
        # 和净利率 25% 的公司不该拿一样的分——那样打出来的 95 分是假的
        got = 0.0
        parts = []
        revenue = _num(fund.get("revenue_growth_yoy"))
        earnings = _num(fund.get("earnings_growth_yoy"))
        margin = _num(fund.get("profit_margin"))
        roe = _num(fund.get("roe"))
        target = _num(fund.get("analyst_target_price"))
        close = _num(snap.get("close"))

        if revenue is not None:
            if revenue >= 20:
                got += 10
            elif revenue >= 10:
                got += 8
            elif revenue >= 3:
                got += 5
            elif revenue > 0:
                got += 2
            else:
                led.risk(f"营收同比下滑 {abs(revenue):.1f}%")
            if revenue > 0:
                parts.append(f"营收同比增长 {revenue:.1f}%")

        if earnings is not None and earnings > 0:
            got += 8 if earnings >= 20 else (6 if earnings >= 10 else 3)
            parts.append(f"利润同比增长 {earnings:.1f}%")

        if margin is not None and margin > 0:
            got += 7 if margin >= 20 else (5 if margin >= 10 else
                                           (3 if margin >= 3 else 1))
            parts.append(f"净利率 {margin:.1f}%")

        if roe is not None and roe > 10:
            got += 5 if roe >= 20 else 3
            parts.append(f"ROE {roe:.1f}%")

        if target and close and target > close:
            upside = (target - close) / close * 100
            got += 5 if upside >= 20 else (3 if upside >= 10 else 1)
            parts.append(f"分析师目标价 {target:.2f}，比现价高 {upside:.0f}%")

        led.add(got, 35, "、".join(parts) if parts else None)

    # 相对强度：离 52 周高点多远
    week_52_high = _num((fund or {}).get("week_52_high"))
    close = _num(snap.get("close"))
    if not week_52_high or not close:
        led.skip("没有 52 周高点数据")
    else:
        gap = (week_52_high - close) / week_52_high * 100
        if gap < 5:
            led.add(20, 20, "贴着 52 周高点")
        elif gap < 10:
            led.add(15, 20, f"离 52 周高点 {gap:.0f}%")
        elif gap < MID_NEAR_52W_HIGH:
            led.add(10, 20, f"离 52 周高点 {gap:.0f}%")
        else:
            led.add(5, 20)
            led.risk(f"离 52 周高点还有 {gap:.0f}%，相对强度弱")

    _score_news(led, news, 20)
    if snap.get("stale"):
        led.risk(f"行情已经 {snap.get('days_since_bar')} 天没更新")
    return led.result()


# --------------------------------------------------------- 止损与目标


def _entry_zone(snap: dict, setup: str) -> tuple[float, float] | None:
    close = _num(snap.get("close"))
    ma_20 = _num(snap.get("ma_20"))
    ma_200 = _num(snap.get("ma_200"))
    if close is None:
        return None
    if setup == "breakout":
        return close * 0.995, close * 1.02
    if setup == "pullback" and ma_20:
        return ma_20 * 0.98, ma_20 * 1.01
    if setup == "oversold_bounce":
        return close * 0.98, close * 1.02
    if setup == "trend_continuation" and ma_20:
        return min(ma_20 * 0.99, close), close * 1.01
    if setup == "bottom_reversal" and ma_200:
        return min(ma_200 * 0.98, close), close * 1.02
    return close * 0.99, close * 1.01


def plan_trade(snap: dict, setup: str) -> dict:
    """给一个形态算出买入区间、止损、两档目标价。

    止损用 ATR 算，不是拍脑袋定个百分比——一只每天晃 8% 的票和一只每天晃 1%
    的票，用同一个 5% 止损，前者天天被扫，后者根本止不住。
    """
    gaps: list[str] = []
    zone = _entry_zone(snap, setup)
    atr = _num(snap.get("atr_14"))

    if zone is None:
        return {"entry_zone": None, "stop": None, "stop_pct": None,
                "targets": [], "rr_ratio": None, "gaps": ["没有价格数据"]}

    entry = (zone[0] + zone[1]) / 2
    if not atr:
        gaps.append("没有 ATR，算不出止损位")
        return {"entry_zone": [round(zone[0], 2), round(zone[1], 2)],
                "stop": None, "stop_pct": None, "targets": [],
                "rr_ratio": None, "gaps": gaps}

    stop = entry - STOP_ATR_MULT * atr
    risk = entry - stop
    targets = [round(entry + m * risk, 2) for m in TARGET_R_MULTIPLES]
    return {
        "entry_zone": [round(zone[0], 2), round(zone[1], 2)],
        "stop": round(stop, 2),
        "stop_pct": round(risk / entry * 100, 2) if entry else None,
        "targets": targets,
        "rr_ratio": TARGET_R_MULTIPLES[0],
        "gaps": gaps,
    }


def position_stop(avg_cost: float, snap: dict) -> dict:
    """已有仓位的止损位：成本下方 2 倍 ATR 和 20 日均线，取高的那个。

    取高的那个是因为：均线在成本上方时，说明已经有浮盈，该把止损抬上来护住；
    均线在下方时，用成本减 ATR 才不会被日常波动扫掉。

    **但仓位深度浮亏时，成本那一档会落在市价上方**——那个止损几周前就破了，
    是历史事实，不是今天能用的价位。所以另给一个 `forward_stop`：要继续拿的话，
    止损该放哪。它永远在市价下方，不然就不叫止损了。
    """
    close = _num(snap.get("close"))
    atr = _num(snap.get("atr_14"))
    ma_20 = _num(snap.get("ma_20"))
    avg_cost = _num(avg_cost)

    candidates = []
    if avg_cost and atr:
        candidates.append(("成本 - 2×ATR", avg_cost - POSITION_STOP_ATR_MULT * atr))
    if ma_20:
        candidates.append(("ma_20", ma_20))
    if not candidates:
        return {"suggested_stop": None, "basis": None, "distance_pct": None,
                "breached": None, "forward_stop": None,
                "gaps": ["没有 ATR 也没有均线，算不出止损"]}

    basis, stop = max(candidates, key=lambda item: item[1])

    # 继续持有的话止损放哪：候选里落在市价下方的取最高的；
    # 全都在市价上方（深度浮亏）就用现价往下 2 个 ATR
    forward_stop = None
    if close is not None:
        below = [level for _, level in candidates if level < close]
        if below:
            forward_stop = round(max(below), 2)
        elif atr:
            forward_stop = round(close - POSITION_STOP_ATR_MULT * atr, 2)

    return {
        "suggested_stop": round(stop, 2),
        "basis": basis,
        "distance_pct": round((close - stop) / close * 100, 2) if close else None,
        "breached": bool(close is not None and close < stop),
        "forward_stop": forward_stop,
        "gaps": [],
    }
