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

## 但是要如实说清楚「这个分是用多少依据打出来的」（评审 C4）

归一化本身没错，错在**没有下限、也没有任何字段说明分母被砍掉了多少**。
2026-08-30 实测的真实后果：平安银行（000001.SZ）走中线档，满分 100 里
基本面 35 分 + 相对强度 20 分整块被移出分母（A股没有基本面数据），剩下的
45 分几乎全来自「价格站在均线上方」，打出来 **82 分**；同一次跑，基本面
齐全、营收 +20.6%、ROE 53.4% 的 ORCL 只有 **77 分**。这两个 82 和 77
放在一起，Claude 会把平安银行排在 Oracle 前面——而它们根本不是同一种
东西：一个是「45% 的评分表打出来的 82」，一个是「100% 的评分表打出来
的 77」。

所以每份评分结果都带上 `evidence_pct`（**这次实际用上的评分项占满分的
百分比**，`_Ledger.possible / 满分`），低于 `LOW_EVIDENCE_PCT` 时还会往
`risks` 里塞一条明写的提醒。

**分数本身不打折**，理由有两条：一是打折等于把「缺数据」重新变成扣分，
正好推翻上面那条归一化的设计；二是打完折之后的分数既不是「拿到的依据
里有几成是好的」、也不是「依据有多充分」，两个意思混在一个数里，现有
所有按分数定的阈值（比如「评分低于 50 要提示」）会全部悄悄变味。
把「依据有多少」单独做成一个看得见的字段，比把它偷偷揉进分数里诚实。

## 单位约定：进来的比率全是百分点

本模块收到的 `snap` 来自 `core/snapshot.py`、`fund` 来自 `core/fundamental.py`。
那两个模块已经在自己的输出边界上做完了换算，交到这里时：

- 所有名字带 `_pct` 的字段都是**百分点**：`dist_ma20_pct = 3.18` 读作
  「高于 20 日均线 3.18%」，`atr_pct = 3.08` 读作「每天平均晃 3.08%」。
- 所有增长率、利润率、ROE、股息率也都是**百分点**：
  `revenue_growth_yoy = 105.9` 读作「营收同比增长 105.9%」。

所以下面这些常数照百分点读就是对的，**一个都不要改**：
`PULLBACK_BAND = (-3.0, 2.0)` 是「离 20 日均线 -3% 到 +2%」，
`OVERSOLD_DIST_MA50 = -8.0` 是「在 50 日均线下方 8% 以上」，
`score_mid()` 里的 `revenue >= 20` 是「营收增长 20% 以上」。
显示串 `f"净利率 {margin:.1f}%"` 也是对的，因为进来的就是百分点。

**注意裸库字段不是这个单位。** `stock_ohlcv_daily.dist_ma20_pct` 和
`stock_fundamentals.revenue_growth_yoy` 在库里存的是小数（0.164 = 16.4%）。
谁要是绕过 core/ 直接查库再把结果喂给本模块，这里所有阈值都会失效——
2026-08-28 实测：小数口径下全市场 2052 只美股 100% 落在 `PULLBACK_BAND` 内，
`oversold_bounce` 永远不可能触发。要喂就先换算成百分点。

`CN_FLOW_FULL_MARK_RATIO = 0.22` 是例外：它比的是主力净流入占成交额的比值，
来自 `core/cn_signals.py` 自己算的 `net / amount`，本来就是小数，不受这条约定管。
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

#: 底部反转：离 52 周高点**至少**这么远才算「还在底部区域」。
#: 跟 `MID_NEAR_52W_HIGH` 是同一个数、方向相反——一只票要么离高点近
#: （趋势延续），要么离高点远（底部反转），不该两头都算。
#: 2026-08-30 实测（2082 只美股）：原来的判据不看价格在不在底部，
#: 命中 353 只，其中 APGE 离 52 周高点 0.0%、CRNX 0.1%、ATKR 0.4%，
#: MSFT 离高点只有 6% 也被标成「底部反转」——贴着 52 周新高的票被贴上
#: 「底部反转」标签，是直接骗人。
MID_BOTTOM_MIN_GAP_FROM_52W_HIGH = 15.0

#: 底部反转：收盘价高出 200 日均线不超过这么多才算「刚站上」。
#: 同一次实测里，命中的票有高出 200 日均线 173% 的——那不是「刚站上
#: 200 日均线」，那是早就走出去半年了。收紧后剩 78 只（`bottom_reversal`
#: 的入场区间本来就是围着 200 日均线给的，只有真在均线附近的票用得上）。
MID_BOTTOM_MAX_ABOVE_MA200 = 15.0

#: 止损放在入场价下方几倍 ATR。不同波动性的票用同一个百分比止损没有意义。
STOP_ATR_MULT = 1.5
#: 持仓止损：成本下方几倍 ATR（比新开仓宽，已经有浮盈浮亏的仓位不该被扫）
POSITION_STOP_ATR_MULT = 2.0
#: 目标价按几倍风险距离给
TARGET_R_MULTIPLES = (2.0, 3.0)

#: 评分实际用上的项占满分不到这个百分比时，往 `risks` 里塞一条明写的
#: 提醒。60% 的意思是「三成以上的评分表根本没参与」——2026-08-30 实测的
#: 真实分布：美股中线档绝大多数票是 100%（基本面齐全），A股中线档是 45%
#: （基本面和相对强度整块缺），中间几乎没有票，阈值放在这个空档里。
LOW_EVIDENCE_PCT = 60

#: 「没有基本面数据」这句话在 `core/` 和 `tools/` 两处都要说，写死成一个
#: 常量，免得两边措辞差一个字就 `sorted(set())` 去不掉重，同一件事在
#: `data_gaps` 里出现两遍（评审顺手修一条）。
NO_FUNDAMENTAL_GAP = "没有基本面数据（A股基本面目前只覆盖 150 只票）"

#: 止损被击穿后，连续多少个交易日之内算「最近发生」——超过这个数就该降级成
#: 「持仓状态」，不再当「立刻处理」的信号。依据见
#: `tools/watch_positions.py` 模块 docstring「止损告警」一节「坑三」：
#: 2026-08-31 用六只真实持仓的分布定的，15~55 个交易日这段区间里当天
#: 没有任何一只票落进去。**两个工具共用这一个数**（`review_positions`
#: 和 `watch_positions`），不允许各自定一个（评审 C1）。
STOP_BREACH_RECENT_DAYS = 15

#: 主力净流入占成交额多少算「满分」。取实测的 90 分位——2026-08-28 当日
#: 净流入为正的 2379 只 A股里，中位数是 9.1%、90 分位是 22.7%。
#: 拍一个 5% 的话第 30 分位就顶格了，等于没分档。
CN_FLOW_FULL_MARK_RATIO = 0.22

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

    # 底部反转：**先得真在底部**，再谈刚站上 200 日均线、均线走平或翘头、
    # 基本面没在恶化。
    #
    # 早期写法完全不检查价格在不在底部，2026-08-30 实测把 MSFT（离 52 周
    # 高点只有 6%）、APGE（离高点 0.0%）这种贴着新高的票也标成「底部
    # 反转」，命中 353 只——一个交易者看到「底部反转」四个字会以为这是
    # 一只跌透了正在修复的票，照着这个印象去买一只 52 周新高附近的票，
    # 是被标签直接误导。所以补两条判据：
    #
    # 1. 离 52 周高点至少 `MID_BOTTOM_MIN_GAP_FROM_52W_HIGH`——不然它是
    #    「趋势延续」那一类，不是底部。**拿不到 52 周高点就不给这个形态**，
    #    判断不了在不在底部就不该贴底部的标签（缺数据不猜，跟本模块
    #    其它地方一致）。
    # 2. 收盘价高出 200 日均线不超过 `MID_BOTTOM_MAX_ABOVE_MA200`——
    #    「刚站上」才叫反转起步，高出一大截说明这波早就走完了，而
    #    `plan_trade()` 给 bottom_reversal 的入场区间是围着 200 日均线
    #    画的，价格离均线太远的话那个区间根本没法用。
    if (close > ma_200 and (slope is None or slope >= -0.5)
            and ma_50 is not None and close > ma_50
            and (revenue_growth is None or revenue_growth > -10)
            and week_52_high
            and (week_52_high - close) / week_52_high * 100
                >= MID_BOTTOM_MIN_GAP_FROM_52W_HIGH
            and (close - ma_200) / ma_200 * 100 <= MID_BOTTOM_MAX_ABOVE_MA200):
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

    def result(self, full_marks: float = 100.0) -> dict:
        """`full_marks` 是这张评分表**没缺任何数据时**的满分（每个评分
        函数把自己那几项的权重加起来传进来，都是 100）。

        `evidence_pct` = 实际进了分母的权重 / 满分——「这个分是用多少
        比例的评分表打出来的」。缺数据不扣分（见模块 docstring），
        但缺了多少必须写在脸上，不然 45% 依据打出来的 82 分会被拿去
        跟 100% 依据打出来的 77 分直接比大小（评审 C4）。
        """
        score = round(self.earned / self.possible * 100) if self.possible else 0
        evidence_pct = (round(self.possible / full_marks * 100)
                        if full_marks else 0)
        risks = list(self.risks)
        if evidence_pct < LOW_EVIDENCE_PCT:
            risks.append(
                f"这个分只用上了 {evidence_pct}% 的评分表——"
                f"{100 - evidence_pct}% 的评分项因为缺数据被移出了分母，"
                f"不能拿它跟依据齐全的票直接比大小")
        return {
            "score": max(0, min(100, score)),
            # 这次实际用上的评分项占满分的百分比。100 = 一项没缺
            "evidence_pct": evidence_pct,
            "reasons": self.reasons,
            "risks": risks,
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
        # 这一档也把数字印出来。四档里只有它不带数字的话，「大致平衡」这句
        # 话就没法跟旁边贴出来的期权流对账（评审 I1 抓到的 S：reasons 写
        # 「大致平衡」，旁边 flow 是 −15.1%，读的人无从判断该信哪个）
        led.add(weight * 0.4, weight, f"期权资金多空大致平衡（净 {tilt:.0%}）")
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
    return led.result(100)


def score_short_cn(snap: dict, fund_flow: dict | None = None,
                   board: dict | None = None, sentiment: dict | None = None,
                   news: dict | None = None) -> dict:
    """A股短波段评分：趋势 25 + 主力资金 30 + 板块强度 25 + 情绪与消息 20。

    A股不是「美股那套的残缺版」。它没有个股期权，但有龙虎榜、主力资金明细和
    强烈的板块轮动。主力资金在这里扮演美股的期权异动——确认突破是真是假。
    """
    led = _Ledger()
    _score_trend(led, snap, 25)

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
            # 连续给分，不分档。分档在顶部必然并列，而 A股这边资金流几乎是
            # 唯一有区分度的项——趋势因为突破条件本身就接近满分，情绪对全市场
            # 是同一个数，板块大多数票拿不到
            earned = 30 * min(ratio / CN_FLOW_FULL_MARK_RATIO, 1.0)
            if net_5d <= 0:
                # 今天流入但 5 日累计还是流出，只是反弹不是持续买入
                earned *= 0.6
                led.risk("当日净流入但 5 日累计仍是净流出，持续性存疑")
            led.add(round(earned, 1), 30,
                    f"主力净流入 {net / 1e8:.2f} 亿，占成交额 {ratio:.1%}"
                    f"（满分线 {CN_FLOW_FULL_MARK_RATIO:.0%}），"
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
    return led.result(100)


def score_mid(snap: dict, fund: dict | None = None,
              news: dict | None = None) -> dict:
    """中线评分：趋势 25 + 基本面 35 + 相对强度 20 + 消息面 20。

    中线才开始看公司好不好。这个函数**有** fund 参数，短线那两个没有。
    """
    led = _Ledger()
    _score_trend(led, snap, 25)

    if not fund:
        led.skip(NO_FUNDAMENTAL_GAP)
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
    return led.result(100)


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


# ------------------------------------------- 「跌破止损」到底是哪一种（评审 C1）
#
# `position_stop()["breached"]` 只是一个布尔值，但它底下藏着三件性质完全
# 不同的事，用同一个最高档报警会把真正紧急的那几只淹掉：
#
# 1. 仓位还在**浮盈**：止损位是 20 日均线（均线抬到成本上方护利润），
#    跌破它是「保护线没了」，不是「这笔交易错了」。
# 2. 仓位**浮亏**、而且**最近**才破位：真正紧急，该按纪律处理。
# 3. 仓位浮亏、但破位已经持续几十个交易日：这是持仓状态，不是今天的新
#    信号。每天当最高优先级重播，用户要么被催着在底部割肉、要么看几天
#    就麻木，把真正「今天刚破」的那条一起忽略掉。
#
# 这两个函数是 `tools/review_positions.py` 和 `tools/watch_positions.py`
# **共用的唯一判定**。评审 C1 抓到的原始事故就是两边各判各的：同一天
# 同一只 AMZN（浮盈 204 美元、只跌破 0.17%），`watch_positions` 说
# 「移动止损被触及、严重度 40、不必马上动」，`review_positions` 说
# 「跌破止损、紧急度 90（最高档）、按纪律该清掉」——用户先读到哪个就信
# 哪个。判定放在这里，两个工具就不可能再各说各话；各自的档位名称和措辞
# 仍然可以不一样（两个工具的语境不同），但**分到哪一类必须是同一个答案**。

STOP_STATE_TRAILING = "trailing_touched"
STOP_STATE_RECENT = "breached_recent"
STOP_STATE_LONGSTANDING = "breached_longstanding"


def consecutive_breach_days(avg_cost: float,
                            daily_rows: list[dict]) -> tuple[int, object]:
    """这只票连续多少个交易日「现价低于当天算出来的止损位」，以及上次
    站上止损位是哪一天。

    `daily_rows` 已经按日期倒序（最新的在前），每行要有 `date` / `close` /
    `ma_20` / `atr_14`。**每一天都用那一天自己的 ma_20/ATR 重新算一遍止损
    位**——不是拿今天的止损位去套过去的价格，过去每天的均线和 ATR 本来就
    不一样。从最新一天开始往前数，遇到第一个「没破位」的日子就停。

    数据不够（比如某天缺 ATR 又缺均线，`position_stop()` 算不出
    `breached`）时也当成「停在这里」处理，不会把 None 当成「破位」继续
    往前数——宁可低估连续天数，不猜。
    """
    count = 0
    last_above = None
    for row in daily_rows:
        snap = {"close": row.get("close"), "atr_14": row.get("atr_14"),
                "ma_20": row.get("ma_20")}
        stop = position_stop(avg_cost, snap)
        if stop.get("breached") is True:
            count += 1
        else:
            last_above = row.get("date")
            break
    return count, last_above


def classify_stop_breach(pnl_amount: float | None, consecutive_days: int,
                         recent_days: int = STOP_BREACH_RECENT_DAYS) -> str:
    """已经确定「跌破止损」之后，判断这是三种情况里的哪一种。

    只在 `position_stop()["breached"]` 为真时调用。

    pnl_amount:       这笔仓位现在的浮动盈亏金额（正数=浮盈）。None 当浮亏
                      处理——不知道盈亏时按更该提醒的那一边走。
    consecutive_days: `consecutive_breach_days()` 数出来的连续破位交易日数。
    """
    if pnl_amount is not None and pnl_amount >= 0:
        return STOP_STATE_TRAILING
    if consecutive_days is None or consecutive_days <= recent_days:
        return STOP_STATE_RECENT
    return STOP_STATE_LONGSTANDING
