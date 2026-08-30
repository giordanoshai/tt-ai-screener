# -*- coding: utf-8 -*-
"""Unusual Whales 实时接口客户端。

## 为什么有些数据走实时、有些走数据库

库里的期权数据一天只灌两次（美东 9:30 和 16:00），拿它「盯盘」名不副实。
所以持仓盯盘和个股分析走实时，全市场扫描走数据库——2000 多只票的均线、RSI、
ATR 库里已经算好了，查一次 0.1 秒；换成实时要调 2000 多次日线接口，
并发 20 路也要 15 分钟，在对话里等不到。

## 两个必须知道的坑

1. **必须带浏览器 User-Agent。** 代理挡在 Cloudflare 后面，Python 默认的
   `Python-urllib/3.12` 会被当成爬虫，直接 403（页面里写的是 Error 1010）。
   这跟 token 没关系，换任何鉴权写法都救不了。
2. **`/api/alerts` 返回的 `symbol` 是期权合约代码**（`NVDA261002P00190000`），
   不是股票代码。标的在 `meta.underlying_symbol` 里。实测 100 条里还有 14 条
   `potus_schedule`，`symbol` 直接是 null。想按持仓过滤必须先解出标的。

## 失败就是 None

这个模块的任何函数**都不抛异常**。取不到就返回 None，调用方据此走降级并在
`data_gaps` 里写明这一路是缺的。一个外部接口慢，不该让整个工具失败。
"""
import concurrent.futures
import json
import urllib.error
import urllib.parse
import urllib.request

import config

#: 不带这个头会被 Cloudflare 403。见模块注释。
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")

#: 并发取多只票时的线程数。6 只持仓一轮 4 秒左右，再高对代理不礼貌。
MAX_WORKERS = 6

#: 期权合约代码（OCC 格式）里，股票代码之后就是 6 位到期日。
#: NVDA261002P00190000 -> NVDA
_OCC_TAIL_LEN = 15


def available() -> bool:
    """有没有配密钥。没配就别调，直接走库里的数据。"""
    return bool(config.UW_API_TOKEN)


def _to_float(value, default: float = 0.0) -> float:
    """UW 把数值全用字符串返回，统一转一下。转不动就给默认值。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get(path: str, params: dict | None = None,
        timeout: float | None = None) -> list[dict] | None:
    """调一个接口，返回 data 数组。任何失败都返回 None，不抛。"""
    if not available():
        return None

    url = config.UW_API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {config.UW_API_TOKEN}",
        "Accept": "application/json",
        "User-Agent": BROWSER_UA,   # 少了这行就是 403，见模块注释
    })
    try:
        with urllib.request.urlopen(
                request, timeout=timeout or config.UW_TIMEOUT) as response:
            payload = json.loads(response.read())
    except Exception:
        return None

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        return data if isinstance(data, list) else None
    return None


def get_many(paths: list[str],
             timeout: float | None = None) -> dict[str, list[dict] | None]:
    """并发调一批接口。键是原样的 path，取不到的那条值是 None。"""
    if not paths:
        return {}
    out: dict[str, list[dict] | None] = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(MAX_WORKERS, len(paths))) as pool:
        futures = {pool.submit(get, p, None, timeout): p for p in paths}
        for future in concurrent.futures.as_completed(futures):
            out[futures[future]] = future.result()
    return out


def flow_alerts(ticker: str, limit: int = 50) -> list[dict] | None:
    """一只票近期的期权异动。实测 3.6 秒。"""
    return get(f"/api/stock/{ticker}/flow-alerts", {"limit": limit})


def flow_alerts_many(tickers: list[str],
                     limit: int = 50) -> dict[str, list[dict] | None]:
    """一批票的期权异动，并发取。键是股票代码。"""
    paths = {f"/api/stock/{t}/flow-alerts?limit={limit}": t for t in tickers}
    raw = get_many(list(paths))
    return {paths[path]: rows for path, rows in raw.items()}


def alerts(limit: int = 100) -> list[dict] | None:
    """用户自己配的实时警报流。实测 12~14 秒，是最慢的一个接口。"""
    return get("/api/alerts", {"limit": limit})


def news_headlines(limit: int = 50) -> list[dict] | None:
    """最近的新闻标题。只有 is_major 布尔值，没有 High/Medium/Low 分级。"""
    return get("/api/news/headlines", {"limit": limit})


def underlying_of(alert: dict) -> str | None:
    """从一条警报里解出股票代码。解不出返回 None。

    优先用 meta.underlying_symbol；没有就从期权合约代码前缀切
    （OCC 格式：代码 + 6 位到期日 + C/P + 8 位行权价 = 后缀恰好 15 位）。
    """
    meta = alert.get("meta") or {}
    underlying = meta.get("underlying_symbol")
    if underlying:
        return str(underlying).upper()

    symbol = alert.get("symbol")
    if not symbol:
        return None
    symbol = str(symbol).upper()
    if len(symbol) > _OCC_TAIL_LEN and symbol[-_OCC_TAIL_LEN:].isalnum():
        head = symbol[:-_OCC_TAIL_LEN]
        if head.isalpha():
            return head
    return symbol if symbol.isalpha() else None


def split_premium(kind: str | None, ask_prem: float,
                  bid_prem: float) -> tuple[float, float]:
    """把一条期权异动的溢价拆成「看多多少、看空多少」。

    白话：在卖价成交说明是买方主动（急着买），在买价成交说明是卖方主动。
    买看涨 = 看多，卖看涨 = 看空；买看跌 = 看空，卖看跌 = 看多。
    """
    if kind == "call":
        return ask_prem, bid_prem
    if kind == "put":
        return bid_prem, ask_prem
    return 0.0, 0.0


def summarize_flow(rows: list[dict] | None) -> dict:
    """把一只票的一堆期权异动汇总成四个数。rows 为空也返回结构完整的零值。"""
    bullish = bearish = biggest = 0.0
    count = 0
    for row in rows or []:
        ask = _to_float(row.get("total_ask_side_prem"))
        bid = _to_float(row.get("total_bid_side_prem"))
        up, down = split_premium(row.get("type"), ask, bid)
        bullish += up
        bearish += down
        biggest = max(biggest, _to_float(row.get("total_premium")))
        count += 1
    return {
        "bullish_premium": round(bullish, 2),
        "bearish_premium": round(bearish, 2),
        "net_premium": round(bullish - bearish, 2),
        "biggest_premium": round(biggest, 2),
        "alert_count": count,
        "source": "uw_live",
    }
