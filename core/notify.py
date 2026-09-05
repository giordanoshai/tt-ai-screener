# -*- coding: utf-8 -*-
"""往 Telegram 推提醒。

## 为什么把发请求那一层单独抽出来

`_post` 是唯一真正发网络请求的函数，测试一律把它换掉。
跟 `core/uw.py` 抽 `_fetch` 是同一个理由：不这么做的话，
配了真 token 的机器上跑一次测试就会真发一条消息给用户。

## 推送失败不能把进程搞挂

盯盘是个要连跑一整天的进程。Telegram 抽风、代理断了、网络闪断，
都只能让这一条丢掉，下一轮继续。所以 `send()` 承诺不抛异常。

## 文案要说清三件事

哪只票、发生了什么、你手上有多少。缺一条用户都得再去查一次，
而盯盘提醒的全部价值就在于「不用再查」。
"""
import json
import sys
import urllib.parse
import urllib.request

import config
from core import net

#: 推送标题的来源标识。这个 Telegram 机器人是跟别的系统共用的
#: （交易执行那套发出来的是「【SOS】✅ 已买入 · …」），所以每条消息
#: 开头必须能一眼认出是谁发的，否则手机上几个系统的通知混在一起没法看。
PREFIX = "【盯盘】"

#: 每类信号配一个图标，扫一眼就知道是好事还是坏事，不用读完整句。
#: 挑的是语义最直白的几个：对勾=成了、红叉=没成、下三角=跌了、
#: 黄牌=还没到但快了。
_KIND_ICON = {
    "接近预警": "⚠️",
    "有效突破": "✅",
    "假突破": "❌",
    "跌破": "🔻",
}

#: 时段代号 → 给人看的说法。盘前盘后要标出来，
#: 那时候流动性差（盘前只占全天成交量 1%~2%），信号成色不一样。
_SESSION_LABEL = {"pr": "盘前", "po": "盘后"}

#: 各类报警的第一行怎么写。
_KIND_LINE = {
    "接近预警": "快到坎了",
    "假突破": "假突破",
    "有效突破": "有效突破",
    "跌破": "跌破",
}


def available() -> bool:
    return bool(config.TG_BOT_TOKEN and config.TG_CHAT_ID)


def _pct(a: float, b: float) -> float:
    """(a-b)/b 的百分数。b 为 0 时返回 0。"""
    return (a - b) / b * 100 if b else 0.0


def format_alert(alert: dict, position: dict | None,
                 today: str | None = None) -> str:
    """把一条报警拼成推送文案。

    `today` 是这只票所属市场的「今天」（`YYYY-MM-DD`，美股按美东算、
    A股按北京算）。给了它，且报价里的 `as_of` 跟它对不上时，文案会
    多一行说明这是哪天的数据——见下面那段注释。
    """
    quote = alert.get("quote") or {}
    last = quote.get("last") or 0.0
    price = alert["price"]
    kind = alert["kind"]

    head = title(_KIND_ICON.get(kind, "•"),
                 "%s · %s" % (_KIND_LINE.get(kind, kind), alert["ticker"]))
    tag = _SESSION_LABEL.get(quote.get("session") or "")
    if tag:
        head += "  [%s]" % tag
    lines = [head]

    if kind == "接近预警":
        lines.append("%.2f 是%s，现价 %.2f，还差 %.1f%%" % (
            price, alert["label"], last, abs(_pct(price, last))))
    elif kind == "假突破":
        high = quote.get("high") or last
        lines.append("冲到 %.2f 越过 %.2f（%s），没站住" % (
            high, price, alert["label"]))
        lines.append("现价 %.2f，已从高点回落 %.1f%%" % (
            last, abs(_pct(last, high))))
    elif kind == "有效突破":
        lines.append("放量站上 %.2f（%s），现价 %.2f" % (
            price, alert["label"], last))
    elif kind == "跌破":
        low = quote.get("low") or last
        lines.append("跌破 %.2f（%s），现价 %.2f，最低到过 %.2f" % (
            price, alert["label"], last, low))

    # 数据不是「今天」的，必须说清楚。2026-09-04 首次上线就撞到了：
    # 盘前 UW 还没有当天的分钟线，取到的是前一天收盘那批，于是推出去两条
    # 「假突破」——内容全对（ORCL 确实在 9/3 冲到 156.77 撞了 156.22 又跌
    # 回 154.38），但那是昨天收盘时发生的事，在盘前看到会以为正在发生。
    # 信息本身有价值，不该压掉；但不标明时点就是误导。
    as_of = quote.get("as_of")
    if today and as_of and as_of != today:
        lines.append("⏰ 这是 %s 收盘时的情况，不是刚刚发生"
                     % as_of[5:].replace("-", "/"))

    if position and position.get("avg_cost"):
        cost = float(position["avg_cost"])
        shares = float(position.get("shares") or 0)
        pnl = _pct(last, cost)
        lines.append("持仓 %g 股 · 成本 %.2f · %s %.1f%%" % (
            shares, cost, "浮盈" if pnl >= 0 else "浮亏", abs(pnl)))

    if tag == "盘后":
        lines.append("盘后流动性低，明早开盘可能反向")
    elif tag == "盘前":
        lines.append("盘前成交稀薄，开盘前仅供参考")

    return "\n".join(lines)


def title(icon: str, headline: str) -> str:
    """拼推送的第一行：`【盯盘】<图标> <说什么事>`。

    统一从这里出，是为了让盯盘发的每一条——报警、启动通知、故障告警——
    在手机上长一个样、一眼认得出来源。
    """
    return "%s%s %s" % (PREFIX, icon, headline)


def _warn(message: str) -> None:
    """往 stderr 写一行。

    用 `print(..., file=...)` 而不是 `sys.stderr.write`，省掉手写换行——
    少一个容易写错的地方。推送这条路上的每一次失败都必须留痕：
    盯盘进程最糟的失败模式就是「在跑，但什么都没发出去」。
    """
    print(message, file=sys.stderr)


#: 代理地址的规范化搬到了 `core/net.py`——UW 那一路也要用同一套规则，
#: 不能有第二份实现。这里保留同名重新导出，别处 `notify.normalize_proxy(...)`
#: 的写法不用改。
normalize_proxy = net.normalize_proxy


def _post(url: str, data: bytes, proxy: str):
    """真正发请求的那一层。异常照抛，由 `send()` 吞掉。

    单独抽出来只为一件事：测试里能整体换掉，绝不真发消息。
    """
    # 没配代理 = 强制直连，连环境变量里的也绕开。推送走哪条路只由 .env
    # 一处说了算，见 `core/net.build_opener` 里 env_when_empty 那段。
    opener = net.build_opener(proxy)
    request = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with opener.open(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def send(text: str) -> bool:
    """推一条消息。任何失败都返回 False，**不抛异常**。

    盯盘进程要连跑一整天，一次推送失败不能让它死掉。
    """
    if not available():
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % config.TG_BOT_TOKEN
    data = urllib.parse.urlencode({
        "chat_id": config.TG_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    proxy = normalize_proxy(config.TG_PROXY)
    if proxy is None:
        # 配了 SOCKS 代理。不能静默走直连——那会超时十几秒然后返回 False，
        # 看日志完全不知道该改什么。说清楚，让人一眼看出问题在哪。
        _warn("推送发不出去：TG_PROXY 配的是 SOCKS 代理（%s），"
              "而 Python 标准库的 urllib 只支持 HTTP/HTTPS 代理。"
              "请改配代理软件的 HTTP 端口（多数软件两个端口同时开着）。"
              % config.TG_PROXY)
        return False
    try:
        _post(url, data, proxy)
        return True
    except Exception as exc:
        _warn("推送失败（%s）：%r" % (
            "经代理 " + proxy if proxy else "直连", exc))
        return False
