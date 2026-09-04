# -*- coding: utf-8 -*-
"""从日线算「关键价位」——头顶的阻力和脚下的支撑。

## 为什么用收盘价找位，不用最高价

这是本模块最重要的一条规则，也是设计阶段第一版翻车的原因。

601138 在 2026-08-28 冲到 66.69 又全吐回来收在 64.04（一根长上影线）。
用「近 N 天最高价」找关键位，66.69 会把下面真正的阻力——8/31 收在
64.84 那个位置——整个盖住。结果是 09-04 那天价格冲到 65.30 撞上真正的
坎又跌回来，程序**一声都不会响**。

66.69 只是一瞬间摸到就吐光的价，它不构成阻力。**市场真正认可、
能收得住的价才是坎。** 所以本模块一律用 close 找局部极值。

## 三个尺度

一个坎不够用：只看半年大高点会漏掉近期的小坎，只看近期又会漏掉
远处的大压力。所以近、中、远各取一档，再按距离筛掉够不着的。

## 单位约定

**这个模块内部一律用小数**（`0.01` 表示 1%），不用百分点。
`CLAUDE.md` 记着这个仓库因为百分点/小数混用炸过三次，
所以这里把话说死：进来的是小数，出去的也是小数，
只有拼推送文案的时候才乘 100。
"""
import datetime

#: 近档窗口：最近 25 个交易日，前后各 2 天判局部高低点。
#: 25 天约一个半月，够覆盖「最近这一波」的高低点；wing 取 2 是因为
#: A 股一周 5 个交易日，前后各 2 天正好是「这一周里最高的那天」。
NEAR_WIN, NEAR_WING = 25, 2

#: 远档窗口：半年，前后各 5 天。wing 更大是为了只留真正显著的顶和底。
FAR_WIN, FAR_WING = 120, 5

#: 远档的位还要被历史反复测试过至少这么多次才算数。
#: 只被碰过一次的价位是偶然，不是坎。
FAR_MIN_TOUCHES = 2

#: 两个位相差多少以内算同一道坎（小数）。1.2% 是实测值：
#: 601138 的 64.84 和 65.00 明显是同一道坎，相差 0.25%；
#: 而 64.84 和 67.42 是两道，相差 4%。1.2% 把这两种情况分得开。
MERGE_BAND = 0.012

#: 离现价超过这么远的位就不盯了（小数）。12% 是一天之内够不着的距离，
#: 盯了只是浪费——每多盯一个位就多一份误报的机会。
MAX_DIST = 0.12

#: 上下各只留最近的几个位。
KEEP_EACH = 2

#: 预警线默认往回退多少（小数）。这个值是比出来的：
#: 601138 在 2026-09-04 昨收 63.20、开盘 63.84，
#: 退 2% 的预警线落在 63.54，**开盘就已经越过**，等于一开盘就叫；
#: 退 1% 落在 64.19，盘中真涨上去才触发，正合适。
DEFAULT_WARN_DIST = 0.01


def local_extremes(bars: list[dict], wing: int, field: str,
                   kind: str) -> list[tuple[datetime.date, float]]:
    """找局部极值：前后各 `wing` 天都不超过（或不低于）它的那些天。

    `field` 一般传 "close"——理由见模块文档。开头和结尾各 `wing` 根
    因为窗口不完整，没法判断，直接跳过。
    """
    out = []
    for i in range(wing, len(bars) - wing):
        value = bars[i].get(field)
        if value is None:
            continue
        window = [bars[j].get(field) for j in range(i - wing, i + wing + 1)]
        if any(w is None for w in window):
            continue
        if (kind == "high" and value >= max(window)) or \
           (kind == "low" and value <= min(window)):
            out.append((bars[i]["date"], value))
    return out


def touch_count(bars: list[dict], price: float, band: float = 0.01) -> int:
    """这个价位历史上被测试过几次——有几天的高低区间罩住了它。

    被反复测试过的价位才是真的坎；只碰过一次的是偶然。
    """
    lo, hi = price * (1 - band), price * (1 + band)
    return sum(1 for b in bars
               if b.get("low") is not None and b.get("high") is not None
               and b["low"] <= hi and b["high"] >= lo)


def merge_levels(items: list[dict], reverse: bool) -> list[dict]:
    """挨得太近的位合并成一个，只保留先到的那个。

    不合并的话同一道坎会报好几次——64.84 和 65.00 实际上是同一个位置。
    `reverse=False` 用于阻力（价格升序，离现价近的在前），
    `reverse=True` 用于支撑（价格降序）。
    """
    if not items:
        return []
    ordered = sorted(items, key=lambda x: x["price"], reverse=reverse)
    out = [ordered[0]]
    for item in ordered[1:]:
        if abs(item["price"] - out[-1]["price"]) / out[-1]["price"] >= MERGE_BAND:
            out.append(item)
    return out


def trim(items: list[dict], keep: int = KEEP_EACH) -> list[dict]:
    """只留最近的几个位。列表必须已经按「离现价从近到远」排好。"""
    return items[:keep]


def warn_line_of(price: float, side: str, warn_dist: float) -> float:
    """预警线：阻力往下退，支撑往上退。

    不能等价格摸到关键位才叫——那时人可能在开会、在吃饭，
    等看到手机它已经冲上去又掉下来了。提前响才有时间反应。
    """
    return price * (1 - warn_dist) if side == "resistance" \
        else price * (1 + warn_dist)


def compute(bars: list[dict], warn_dist: float = DEFAULT_WARN_DIST) -> dict:
    """从日线算出该盯的关键位。

    `bars` 按日期正序，最后一根最新，每行要有 date/open/high/low/close/volume。
    数据不够长就返回空——窗口不完整时算出来的位不可信，宁可不报。

    返回 `{"resistance": [...], "support": [...]}`，每个位形如：
        {"price": 64.84, "label": "近期高收08-31",
         "warn_line": 64.19, "side": "resistance"}
    """
    if len(bars) < FAR_WIN + FAR_WING:
        return {"resistance": [], "support": []}

    last_close = bars[-1].get("close")
    if not last_close:
        return {"resistance": [], "support": []}

    resistance, support = [], []

    # 近档：最近 25 天的收盘价局部高低点
    near = bars[-NEAR_WIN:]
    for day, value in local_extremes(near, NEAR_WING, "close", "high"):
        resistance.append({"price": value,
                           "label": "近期高收%s" % day.strftime("%m-%d")})
    for day, value in local_extremes(near, NEAR_WING, "close", "low"):
        support.append({"price": value,
                        "label": "近期低收%s" % day.strftime("%m-%d")})

    # 中档：20 日最高/最低收盘
    mid = bars[-20:]
    mid_closes = [b["close"] for b in mid if b.get("close") is not None]
    if mid_closes:
        resistance.append({"price": max(mid_closes), "label": "20日最高收"})
        support.append({"price": min(mid_closes), "label": "20日最低收"})

    # 远档：半年局部高低点，且被测试过至少 2 次
    far = bars[-FAR_WIN:]
    for day, value in local_extremes(far, FAR_WING, "close", "high"):
        if touch_count(far, value) >= FAR_MIN_TOUCHES:
            resistance.append({"price": value,
                               "label": "半年高%s" % day.strftime("%m-%d")})
    for day, value in local_extremes(far, FAR_WING, "close", "low"):
        if touch_count(far, value) >= FAR_MIN_TOUCHES:
            support.append({"price": value,
                            "label": "半年低%s" % day.strftime("%m-%d")})

    # 方向和距离过滤：阻力必须在现价之上、支撑在现价之下，且都在 12% 以内
    resistance = [x for x in resistance
                  if last_close < x["price"] <= last_close * (1 + MAX_DIST)]
    support = [x for x in support
               if last_close * (1 - MAX_DIST) <= x["price"] < last_close]

    resistance = trim(merge_levels(resistance, reverse=False))
    support = trim(merge_levels(support, reverse=True))

    for item in resistance:
        item["side"] = "resistance"
        item["warn_line"] = warn_line_of(item["price"], "resistance", warn_dist)
    for item in support:
        item["side"] = "support"
        item["warn_line"] = warn_line_of(item["price"], "support", warn_dist)

    return {"resistance": resistance, "support": support}
