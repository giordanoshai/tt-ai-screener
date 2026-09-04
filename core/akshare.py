# -*- coding: utf-8 -*-
"""AKShare（自建 AKTools 服务）实时客户端。目前只做一件事：A股小时线。

## 为什么小时线要走实时

库里的 A股小时线只覆盖【活跃池】（约 150 只），而活跃池的粗筛条件是
「站上 50 日均线 + 放量」——那是在挑今天强势的票，跟「用户想看哪只票」
完全是两回事。2026-09-01 实测：贵州茅台、五粮液、宁德时代、比亚迪，
四只最有名的 A股，库里一根小时线都没有。

新浪的 60 分钟线**一次调用给约 1970 根**（覆盖两年），2~3 秒，而
`tools/hourly.py` 最多只要 200 根。为这点需求去预采全池 1500 只
（约 145 万行、每天多跑 10 分钟）不划算，所以改成【用到谁拉谁】，
库里那 150 只留着当兜底。

这不违反「本仓库只读」的规矩：只读指的是不往库里写行情，不是不能读实时
接口——`core/uw.py` 早就在直连 Unusual Whales 了。

## 失败就是空列表

跟 `core/uw.py` 一个规矩：这个模块**任何函数都不抛异常**。取不到就返回空，
调用方退回数据库。一个外部接口慢，不该让整个工具失败。

## `ts` 是这根 K 线【开始】的时刻

新浪标的是【结束】时刻——实测 sh600519 一次返回 1970 行，`day` 只出现
10:30 / 11:30 / 14:00 / 15:00 四种，一行 09:30 都没有。A股 9:30 开盘，
第一根覆盖 9:30~10:30 却被标成 10:30，所以这里减 60 分钟。

tt-data-hub 的 `hub/sources/akshare.py::parse_minute_60` 是同一套换算。
两个仓库写进/读出同一张 `stock_ohlcv_min`，语义必须一致——**改这边记得
改那边**，错一根的话所有点位整体错一格。
"""
import datetime
import json
import math
import sys
import urllib.parse
import urllib.request

import config

#: A股代码后缀 → 新浪的前缀。
_SUFFIX_TO_SINA = {"SH": "sh", "SZ": "sz", "BJ": "bj"}

#: 一根 60 分钟线的长度，用来把结束时刻换算回开始时刻。
BAR_MINUTES_60 = 60

#: 北京时间。A股没有夏令时，固定 +8。
CN_TZ = datetime.timezone(datetime.timedelta(hours=8))

#: 小时线的超时。单次调用本身要 2~3 秒，给足但不能让对话干等。
TIMEOUT_SECONDS = 20

#: 全市场快照（`stock_zh_a_spot`）的超时，比小时线宽得多，故意的。
#: 实测这个接口一趟拉回 5555 只要 15 秒——20 秒只剩 5 秒余量，而这台
#: AKTools 跟 tt-data-hub 的采集任务是同一个服务，采集跑起来的时候会
#: 明显更慢。超时了就是空列表，盯盘那头 A股静悄悄地不再被盯，
#: 所以宁可多等也别为省几十秒把整条路弄哑。
SPOT_TIMEOUT_SECONDS = 60


def available() -> bool:
    """有没有配 AKTools 地址。没配就别调，直接走库里的数据。"""
    return bool(config.AKSHARE_BASE_URL)


def to_sina(ticker: str) -> str | None:
    """`600519.SH` → `sh600519`。不是 A股代码返回 None。"""
    parts = (ticker or "").strip().upper().split(".")
    if len(parts) != 2:
        return None
    code, suffix = parts
    prefix = _SUFFIX_TO_SINA.get(suffix)
    if not prefix or not code.isdigit() or len(code) != 6:
        return None
    return f"{prefix}{code}"


def _get(func: str, params: dict, timeout: float | None = None):
    """调一个 AKShare 函数。网络层的异常照抛，由公开函数统一吞掉。

    `timeout` 不传就沿用 `TIMEOUT_SECONDS`（按小时线定的 20 秒），
    这样 `hourly()` 的行为一个字都没变；慢接口自己传一个更大的值。
    """
    url = (f"{config.AKSHARE_BASE_URL.rstrip('/')}/{func}"
           f"?{urllib.parse.urlencode(params)}")
    # AKTools 是局域网里的自建服务，显式绕开代理——本机装着用户级
    # HTTP_PROXY，不绕开的话 urllib 会拿它去连局域网地址，报莫名其妙的 503。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout or TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def spot_snapshot() -> list[dict]:
    """A股全市场实时快照，一趟拿回约 5555 只。取不到返回空列表，不抛异常。

    盯盘（`core/quotes.py`）靠它取实时价：**盯 1 只和盯 5000 只是同一个
    请求**，所以不按票拉。

    只能走新浪的 `stock_zh_a_spot`：2026-09-04 实测这台 AKTools 上所有
    `_em`（东方财富）后缀接口全部 500。也不能改用小时线——新浪的小时线
    对当天盘中那几根只给成交量、不给价格。

    失败时会往 stderr 写一行。**这条日志是有意加的**：盯盘进程失败后
    只是返回空，看上去一切正常，不出声的话「AKTools 慢了 5 秒 → A股
    从此不再被盯」这种事没有任何迹象。
    """
    if not available():
        return []
    try:
        rows = _get("stock_zh_a_spot", {}, timeout=SPOT_TIMEOUT_SECONDS)
    except Exception as exc:
        sys.stderr.write("A股全市场快照取不到（AKTools）：%r\n" % (exc,))
        return []
    return rows if isinstance(rows, list) else []


def _price(value):
    """转成正的有限浮点数，转不成返回 None。

    必须挡住 NaN：`float('nan')` 既不是假值、也不满足 `<= 0`，
    `if not x or x <= 0` 这种写法会放它过去，一路算进均线。
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) and num > 0 else None


def _volume(value) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    return num if math.isfinite(num) else 0.0


def parse_minute_60(rows: list) -> list[dict]:
    """把新浪 60 分钟线的返回转成**跟库里那条 SQL 一样形状**的行。纯函数。

    形状要对齐 `tools/hourly.py::HOURLY_SQL`：ts / ts_local / session /
    open / high / low / close / volume / market。对不齐的话调用方就得为
    「实时」和「库里」写两套解析。

    两件保命的事：
      1. 四个价格一起校验，缺一个就整根丢掉——不能拿 None 去算均线。
      2. 按时间戳去重，重复的留最后一条。
    """
    by_ts: dict[datetime.datetime, dict] = {}

    for raw in rows or []:
        prices = {name: _price(raw.get(name))
                  for name in ("open", "high", "low", "close")}
        if any(value is None for value in prices.values()):
            continue
        try:
            label = datetime.datetime.fromisoformat(str(raw.get("day", "")))
        except (TypeError, ValueError):
            continue
        # 新浪标的是结束时刻，减一根的长度换成开始时刻。见模块注释。
        ts = (label.replace(tzinfo=CN_TZ)
              - datetime.timedelta(minutes=BAR_MINUTES_60))
        by_ts[ts] = {
            "ts": ts,
            # 库里那列是 `ts at time zone 'Asia/Shanghai'`，出来不带时区
            "ts_local": ts.replace(tzinfo=None),
            "session": "RTH",     # A股没有盘前盘后，采集侧写的也是 RTH
            "market": "CN",
            "volume": _volume(raw.get("volume")),
            **prices,
        }

    return sorted(by_ts.values(), key=lambda r: r["ts"])


def hourly(ticker: str, bars: int = 60) -> list[dict]:
    """一只 A股最近 `bars` 根小时线，正序（最后一根最新）。

    取不到就返回空列表，**不抛异常**——调用方据此退回数据库。
    """
    if not available():
        return []
    symbol = to_sina(ticker)
    if not symbol:
        return []
    try:
        raw = _get("stock_zh_a_minute",
                   {"symbol": symbol, "period": "60", "adjust": "qfq"})
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return parse_minute_60(raw)[-max(int(bars), 1):]
