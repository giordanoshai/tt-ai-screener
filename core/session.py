# -*- coding: utf-8 -*-
"""判断此刻是哪个市场的哪个交易时段。

## A股只有盘中，美股有盘前盘后

这是两个市场最大的区别。合起来一天要盯 20 小时，
只有北京时间 08:00-09:30 和 11:30-13:00 是空的。

    09:30-11:30   A股上午
    13:00-15:00   A股下午
    16:00-21:30   美股盘前     （夏令时）
    21:30-04:00   美股常规     ← 成交量占 80% 的主战场
    04:00-08:00   美股盘后

**冬令时美股三段各顺延 1 小时**，所以边界必须按日期算，不能写死。
美国夏令时是 3 月第二个周日到 11 月第一个周日。

## 盘前为什么要区别对待

实测 AAPL 连续三天，盘前成交量只占全天的 1.21%~2.20%
（常规 75%~81%，盘后 17%~23%）。几百股就能把价格打出去一大截，
这时候的「突破」极可能是假的。所以盘前要更多采样点确认。

## 一律用 UTC 算

传进来的时刻必须带时区。内部换算成北京时间（A股）和美东时间（美股），
不依赖运行机器的本地时区——服务器时区改了不该影响判断。
"""
import datetime

#: 北京时间。A股没有夏令时，固定 +8。
CN_TZ = datetime.timezone(datetime.timedelta(hours=8))

#: A股的两段，北京时间的「几点几分」用分钟数表示（9:30 = 570）。
CN_SPANS = ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60))

#: 美股三段，**美东当地时间**的分钟数。冬夏令时的差别体现在
#: 美东和 UTC 的偏移上（见 _us_tz），所以这张表本身不用管。
US_SPANS = {
    "us_pre": (4 * 60, 9 * 60 + 30),       # 04:00-09:30
    "us_rth": (9 * 60 + 30, 16 * 60),      # 09:30-16:00
    "us_post": (16 * 60, 20 * 60),         # 16:00-20:00
}

#: 各时段要连续几个采样点确认才算真触发。
#: 盘前流动性差（只占全天成交量 1%~2%），几百股就能打出一个假价格，
#: 所以要求更多确认。
CONFIRM_TICKS = {"us_pre": 3, "default": 2}

#: 各时段全天有多少分钟——盘中量比按时间折算时要用。
#: A股 4 小时 = 240 分钟，美股常规 6.5 小时 = 390 分钟。
SESSION_MINUTES = {"cn": 240, "us_rth": 390}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime.date:
    """某年某月的第 n 个星期几。weekday 用 Monday=0 的口径，6 是周日。"""
    first = datetime.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + datetime.timedelta(days=offset + 7 * (n - 1))


def us_is_dst(day: datetime.date) -> bool:
    """这一天美国是不是夏令时。

    规则：3 月第二个周日开始，11 月第一个周日结束。
    切换发生在当地凌晨 2 点，但对本模块来说按天判断就够——
    那个时刻三个时段都不在交易，差一小时不影响任何判断。
    """
    start = _nth_weekday(day.year, 3, 6, 2)
    end = _nth_weekday(day.year, 11, 6, 1)
    return start <= day < end


def _us_tz(day: datetime.date) -> datetime.timezone:
    """美东时区。夏令时 UTC-4，冬令时 UTC-5。"""
    hours = -4 if us_is_dst(day) else -5
    return datetime.timezone(datetime.timedelta(hours=hours))


def _in_span(minutes: int, span: tuple) -> bool:
    return span[0] <= minutes < span[1]


def current(now_utc: datetime.datetime) -> list[str]:
    """此刻正在进行的交易时段，可能不止一个，都不在就返回空列表。

    周末一律不算。节假日不判断——多跑几轮的成本只是几个请求，
    不值得为此维护一张年年要更新的节假日表。
    """
    out = []

    cn_now = now_utc.astimezone(CN_TZ)
    if cn_now.weekday() < 5:
        minutes = cn_now.hour * 60 + cn_now.minute
        if any(_in_span(minutes, span) for span in CN_SPANS):
            out.append("cn")

    us_tz = _us_tz(now_utc.date())
    us_now = now_utc.astimezone(us_tz)
    if us_now.weekday() < 5:
        minutes = us_now.hour * 60 + us_now.minute
        for name, span in US_SPANS.items():
            if _in_span(minutes, span):
                out.append(name)

    return out


def is_premarket(name: str) -> bool:
    """是不是盘前——盘前的信号要更多确认，且不判「有效突破」。"""
    return name == "us_pre"


def confirm_ticks(name: str) -> int:
    """这个时段要连续几个采样点确认。"""
    return CONFIRM_TICKS.get(name, CONFIRM_TICKS["default"])
