# -*- coding: utf-8 -*-
"""把「价格 + 关键位 + 时段」变成「要不要叫醒用户」。

## 碰到价不等于要动手

同样是碰到 64.84，冲过去站住和冲过去掉下来是完全相反的两件事。
所以碰到之后还要看它怎么走，分成四种结果，只有三种值得推送：

    有效突破   收在关键位上方 + 放量      → 推
    假突破     盘中越过了又跌回下方        → 推   ← 601138 在 09-04 就是这个
    跌破       收在支撑位下方             → 推
    站上没量 / 受阻回落 / 探底守住          → 不推，是流水账

设计阶段实测：把后三种也推出去，45 个交易日里 73% 的天都在响；
只推前三种，降到 31%。一个吵的提醒工具，用户两天就静音，等于没有。

## 预警必须是「穿越触发」

不能等价格摸到关键位才叫——那时人可能在开会、在吃饭。每个关键位
往回退 1% 画一条预警线，但**只在价格从远处走过来、穿过那条线的
一刻才报**；如果价格本来就在附近磨蹭，一声不响。
这一条把预警从 27 条压到 17 条，是降噪最有效的一招。

## 盘中量比必须按时间折算

这是实现时最容易翻车的地方。盘中成交量是累计值，上午 10 点只跑了
半小时，直接除以 20 日均量**永远偏低**，结果「有效突破」这一类
**永远判不出来**。必须按已开盘的时间比例折算。

开盘头 15 分钟分母太小、量比剧烈跳动，那段时间不做突破判定，
只判「假突破」和「跌破」。

## 单位一律用小数

`0.005` 表示 0.5%。见 `core/levels.py` 的同款说明。
"""
import datetime

from core import session as session_mod

#: 只有这三类值得打扰用户，其余是流水账。
ACTIONABLE = ("有效突破", "假突破", "跌破")

#: 进到关键位的 ±0.5% 以内就算「碰到」。
TOUCH_BAND = 0.005

#: 同一个位的到位判定，多少天内不重复。
FIRE_COOLDOWN_DAYS = 5

#: 同一个位的接近预警，多少天内不重复。预警冷却更长——
#: 「快到坎了」这件事说一遍就够，说三遍就是噪音。
WARN_COOLDOWN_DAYS = 10

#: 量比到这个倍数才算「放量」。
VOL_CONFIRM = 1.2

#: 开盘头多少分钟不做突破判定（量比分母太小，跳动剧烈）。
SKIP_OPEN_MINUTES = 15

#: 保险丝：每只票每天最多推几条、全天总共最多几条。
#: 实测频率是每只票每天 0.78 条、7 只持仓一天 5~6 条，离这两个数很远。
#: 它们不是用来调节奏的，是防止算法出 bug 时把用户的手机炸掉——
#: 真到了上限，说明有别的东西坏了。
MAX_PER_TICKER_PER_DAY = 4
MAX_PER_DAY = 25


def intraday_volume_ratio(volume: float, avg_volume: float,
                          elapsed_minutes: float,
                          session_name: str) -> float | None:
    """盘中量比，按已开盘时间折算。算不了就返回 None。

    不折算的话，开盘半小时的成交量除以全天均量永远是个很小的数，
    「有效突破」永远判不出来——这是设计文档 5.1 节点名的翻车点。
    """
    total = session_mod.SESSION_MINUTES.get(session_name)
    if not total or not avg_volume or elapsed_minutes < SKIP_OPEN_MINUTES:
        return None
    expected = avg_volume * (min(elapsed_minutes, total) / total)
    if expected <= 0:
        return None
    return volume / expected


def classify(side: str, price: float, quote: dict,
             vol_ratio: float | None, premarket: bool) -> str | None:
    """碰到这个位之后走成了什么样。不值得推的返回 None。"""
    last = quote.get("last")
    if not last:
        return None

    if side == "resistance":
        high = quote.get("high") or last
        if last > price:
            # 盘前不判「有效突破」：成交量只占全天 1%~2%，量能不可比
            if premarket or vol_ratio is None:
                return None
            return "有效突破" if vol_ratio > VOL_CONFIRM else None
        if high > price:
            return "假突破"        # 冲过去了又跌回来
        return None                # 受阻回落，是流水账

    if last < price:
        return "跌破"
    return None                    # 探底守住，是流水账


def crossed_warn_line(side: str, warn_line: float,
                      prev_last: float | None, now_quote: dict) -> bool:
    """价格是不是**刚刚**穿过预警线（从远处走过来）。

    在预警线附近来回磨蹭不算——那会把同一件事报很多遍。
    没有上一个采样点（进程刚起）时不报，等下一轮。
    """
    last = now_quote.get("last")
    if not last or prev_last is None:
        return False
    if side == "resistance":
        return prev_last < warn_line <= last
    return prev_last > warn_line >= last


def _touched(side: str, price: float, quote: dict) -> bool:
    if side == "resistance":
        high = quote.get("high") or quote.get("last") or 0
        return high >= price * (1 - TOUCH_BAND)
    low = quote.get("low") or quote.get("last") or 0
    return low <= price * (1 + TOUCH_BAND)


def _key(ticker: str, price: float) -> str:
    return "%s|%.2f" % (ticker, price)


def _on_cooldown(store: dict, key: str, today: str, days: int) -> bool:
    """同一个位在冷却期内不重复报。存日期字符串，够用且好读。"""
    last_day = store.get(key)
    if not last_day:
        return False
    try:
        prev = datetime.date.fromisoformat(last_day)
        now = datetime.date.fromisoformat(today)
    except ValueError:
        return False
    return (now - prev).days < days


def evaluate(ticker: str, quote: dict, levels: dict, state: dict,
             session_name: str, avg_volume: float,
             elapsed_minutes: float, today: str | None = None) -> list[dict]:
    """算出这一轮该推的报警。会就地更新 `state`（去重用）。

    `state` 形如：
        {"fired": {"票|价": "YYYY-MM-DD"},
         "warned": {...},
         "prev_last": {"票": 上一轮的现价},
         "ticks": {"票|价": 连续命中了几次}}
    """
    if today is None:
        today = datetime.date.today().isoformat()

    premarket = session_mod.is_premarket(session_name)
    need_ticks = session_mod.confirm_ticks(session_name)
    vol_ratio = intraday_volume_ratio(
        quote.get("volume") or 0.0, avg_volume, elapsed_minutes, session_name)
    prev_last = state.setdefault("prev_last", {}).get(ticker)
    ticks = state.setdefault("ticks", {})

    out = []
    items = list(levels.get("resistance") or []) + list(levels.get("support") or [])
    for item in items:
        side, price = item["side"], item["price"]
        key = _key(ticker, price)

        # ---- 接近预警：穿过预警线的那一刻 ----
        if crossed_warn_line(side, item["warn_line"], prev_last, quote):
            if not _on_cooldown(state.setdefault("warned", {}), key,
                                today, WARN_COOLDOWN_DAYS):
                state["warned"][key] = today
                out.append({"ticker": ticker, "kind": "接近预警",
                            "price": price, "label": item["label"],
                            "side": side, "quote": quote,
                            "session": session_name})

        # ---- 到位判定：碰到之后看它怎么走 ----
        if not _touched(side, price, quote):
            ticks.pop(key, None)
            continue
        kind = classify(side, price, quote, vol_ratio, premarket)
        if kind not in ACTIONABLE:
            ticks.pop(key, None)
            continue

        # 连续命中够几次才算数——盘前要 3 次，别的 2 次。
        #
        # 计数按「位」记，**不按 kind 记**，这是有意的：价格在关键位上下
        # 来回时，同一个位的状态可能从「有效突破」变成「假突破」，
        # 而用户要知道的是**最后落在哪一边**。按位计数、报最新那次的
        # 判定，正好给出这个答案；按 kind 分开计数反而会两种都报一遍。
        ticks[key] = ticks.get(key, 0) + 1
        if ticks[key] < need_ticks:
            continue
        if _on_cooldown(state.setdefault("fired", {}), key,
                        today, FIRE_COOLDOWN_DAYS):
            continue
        state["fired"][key] = today
        ticks[key] = 0
        out.append({"ticker": ticker, "kind": kind, "price": price,
                    "label": item["label"], "side": side,
                    "quote": quote, "session": session_name})

    state["prev_last"][ticker] = quote.get("last")
    return out
