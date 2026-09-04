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
import urllib.parse
import urllib.request

import config

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


def format_alert(alert: dict, position: dict | None) -> str:
    """把一条报警拼成推送文案。"""
    quote = alert.get("quote") or {}
    last = quote.get("last") or 0.0
    price = alert["price"]
    kind = alert["kind"]

    head = "%s · %s" % (alert["ticker"], _KIND_LINE.get(kind, kind))
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


def _post(url: str, data: bytes, proxy: str):
    """真正发请求的那一层。异常照抛，由 `send()` 吞掉。

    单独抽出来只为一件事：测试里能整体换掉，绝不真发消息。
    """
    handler = urllib.request.ProxyHandler(
        {"http": proxy, "https": proxy} if proxy else {})
    opener = urllib.request.build_opener(handler)
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
    try:
        _post(url, data, config.TG_PROXY)
        return True
    except Exception:
        return False
