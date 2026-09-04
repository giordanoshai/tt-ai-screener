# -*- coding: utf-8 -*-
"""取实时价。两个市场两条完全不同的路，统一成一个结构交出去。

## A股：一次拉回全市场

走新浪的 `stock_zh_a_spot`，一趟 15 秒拿回 5555 只，**盯 1 只和盯
5000 只一样快**。所以不管要几只票，都是同一个请求。

**不能用东方财富源。** 2026-09-04 实测这台 AKTools 上所有 `_em` 后缀
接口全部返回 500（`stock_zh_a_spot_em`、`stock_bid_ask_em`、
`stock_zh_a_hist_min_em`、`stock_zh_index_spot_em`），新浪源全部正常。

**也不能用小时线。** 新浪的小时线对**当天盘中**的 K 线只给成交量、
不给价格（OHLC 全是 None），拿不到现价。

## 美股：按票并发拉 1 分钟线

UW **没有「当前价」接口**——`/info`、`/quote`、`/price`、`/spot`
四个路径实测全部返回 None。只能取 `/api/stock/{票}/ohlc/1m`
的最新一根。

**UW 返回的是倒序，第一行最新。** 取末行会拿到一个月前的价格。

单票 11.4 秒，但并发有效：6 只一起拉只要 13.8 秒。

## 失败一律返回空，不抛异常

任何一路挂了都只能影响那一路，不能让整轮盯盘死掉。
`tests/conftest.py` 默认把底层取数换成抛异常的桩，正是靠这条约定
才不会把所有测试打红。
"""
import datetime
import math

from core import akshare, session, uw

#: 新浪快照里我们要的字段 → 统一结构的键名。
_SINA_FIELDS = {
    "最新价": "last", "最高": "high", "最低": "low",
    "今开": "open", "昨收": "prev_close", "成交量": "volume",
}


def _f(value):
    """转成有限浮点数，转不动或是 NaN/Inf 返回 None。

    必须挡住 NaN：`float('nan')` 既不是假值也不满足比较，
    放过去会一路算进关键位判定。
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def cn_quotes(tickers: list[str]) -> dict[str, dict]:
    """A股实时价。一个请求拿回全市场，再从里面挑我们要的票。"""
    if not tickers or not akshare.available():
        return {}
    wanted = {}
    for ticker in tickers:
        sina = akshare.to_sina(ticker)      # 600519.SH -> sh600519
        if sina:
            wanted[sina] = ticker
    if not wanted:
        return {}

    try:
        rows = akshare._get("stock_zh_a_spot", {})
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}

    out = {}
    for row in rows:
        code = str(row.get("代码", "")).strip().lower()
        ticker = wanted.get(code)
        if not ticker:
            continue
        quote = {"session": "cn", "source": "akshare"}
        for cn_key, key in _SINA_FIELDS.items():
            quote[key] = _f(row.get(cn_key))
        if quote.get("last"):
            out[ticker] = quote
    return out


def _parse_utc_time(ts_str):
    """把 ISO 8601 UTC 时间解析成 datetime.datetime（带 UTC 时区）。

    接受 "2026-09-03T23:59:00Z" 这样的格式。
    解析失败返回 None。
    """
    try:
        # fromisoformat 不认 Z 后缀，先改成 +00:00
        normalized = ts_str.replace("Z", "+00:00") if ts_str.endswith("Z") else ts_str
        return datetime.datetime.fromisoformat(normalized)
    except (ValueError, AttributeError):
        return None


def us_quotes(tickers: list[str]) -> dict[str, dict]:
    """美股实时价。并发拉 1 分钟线，取最新一根当现价。

    高低价、开盘价只反映**最新交易日**（美东日期）的数据，不跨日。
    """
    if not tickers or not uw.available():
        return {}
    # 指数代码喂给 /api/stock/... 一律 422，发出去只是白等十几秒
    paths = {"/api/stock/%s/ohlc/1m" % t: t
             for t in tickers if not t.startswith("^")}
    if not paths:
        return {}

    try:
        payloads = uw.get_many(list(paths.keys()))
    except Exception:
        return {}

    out = {}
    for path, rows in (payloads or {}).items():
        ticker = paths.get(path)
        if not ticker or not isinstance(rows, list) or not rows:
            continue
        newest = rows[0]                   # UW 是倒序，第一行最新
        last = _f(newest.get("close"))
        if not last:
            continue

        # 按美东日期分组，只保留最新的一天
        # 因为冬令时会跨 UTC 午夜，不能用 UTC 日期分组
        rows_by_us_date = {}
        latest_us_date = None
        for row in rows:
            utc_time = _parse_utc_time(row.get("start_time"))
            if not utc_time:
                continue
            # 转换到美东时间（根据那一天的夏冬令时）
            us_tz = session._us_tz(utc_time.date())
            us_time = utc_time.astimezone(us_tz)
            us_date = us_time.date()
            if us_date not in rows_by_us_date:
                rows_by_us_date[us_date] = []
            rows_by_us_date[us_date].append(row)
            if latest_us_date is None or us_date > latest_us_date:
                latest_us_date = us_date

        # 只用最新交易日的数据
        if not latest_us_date or latest_us_date not in rows_by_us_date:
            # 没有日期信息，就用全部行的兜底策略
            latest_rows = rows
        else:
            latest_rows = rows_by_us_date[latest_us_date]

        highs = [h for h in (_f(r.get("high")) for r in latest_rows) if h]
        lows = [l for l in (_f(r.get("low")) for r in latest_rows) if l]
        # open 是最新交易日最早（列表末尾）那根的开盘价
        latest_open = None
        if latest_rows:
            latest_open = _f(latest_rows[-1].get("open"))

        out[ticker] = {
            "last": last,
            # 当天（最新交易日）最高最低扫过所有分钟线——「假突破」判定要用盘中最高价
            "high": max(highs) if highs else last,
            "low": min(lows) if lows else last,
            "open": latest_open or last,
            "prev_close": None,            # 分钟线里没有昨收，本功能用不到
            "volume": _f(newest.get("total_volume")) or 0.0,
            "session": newest.get("market_time") or "r",
            "source": "uw",
        }
    return out


def get(tickers_by_market: dict[str, list[str]]) -> dict[str, dict]:
    """取一批票的实时价，按市场自动分流。

    某一个市场挂了不影响另一个——这是盯盘进程能一直活着的前提。
    """
    out = {}
    try:
        out.update(cn_quotes(tickers_by_market.get("CN") or []))
    except Exception:
        pass
    try:
        out.update(us_quotes(tickers_by_market.get("US") or []))
    except Exception:
        pass
    return out
