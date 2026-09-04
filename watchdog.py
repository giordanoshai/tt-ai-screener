# -*- coding: utf-8 -*-
"""盯盘守护进程：盘中每分钟看一眼持仓，撞上关键价位就推 Telegram。

## 为什么独立跑，不塞进 main.py

MCP 服务是给 Claude.ai 用的。用户重启它、改代码、它偶尔崩，
**都不应该让盯盘停掉**；反过来盯盘每分钟拉一轮行情，
也不该拖累 MCP 的响应。两件事的失败模式完全不同。

## 每一轮做什么

    1. 从 user_positions 读当前持仓  ← 每轮都读，不缓存
    2. 按市场分两拨取实时价
    3. 跟当天的关键位比对
    4. 触发了、且没在冷却期 → 推 Telegram

**持仓每轮都重新读**是用户明确要的：他在 Claude.ai 里加一笔仓，
下一分钟盯盘就得认；平了仓，下一分钟就不该再盯。这个查询才几行，
成本可以忽略。

## 这个进程只读数据库

读 `user_positions` 和 `stock_ohlcv_daily`，一张表都不写。

用法：
    python watchdog.py            # 一直跑
    python watchdog.py --once     # 只跑一轮，调试用
"""
import argparse
import datetime
import json
import sys
import time

import config
from core import alerts, levels, notify, quotes, session as session_mod
from db import pg

UTC = datetime.timezone.utc

#: 取多少根日线来算关键位。远档窗口要 120 根 + 前后各 5 根余量，
#: 200 根留了充分空间，也不至于把查询拖慢。
BARS_LIMIT = 200

#: 关键位一天算一次就够（它只依赖收盘数据），缓存在内存里。
#: 键是 "票|市场|日期"，换天了整个清掉。
_levels_cache: dict = {}


def empty_state() -> dict:
    # daily 形如 {"date": "2026-09-04", "total": 3, "by_ticker": {"票": 2}}，
    # 换天自动清零。它是保险丝，见 _within_daily_cap。
    return {"fired": {}, "warned": {}, "prev_last": {}, "ticks": {},
            "daily": {}}


def _within_daily_cap(state: dict, ticker: str, today: str) -> bool:
    """今天这只票、以及全天总量，还没到上限就放行，并记一笔。

    实测频率是每只票每天 0.78 条、7 只持仓一天 5~6 条，离上限很远。
    这两道闸不是用来调节奏的，是防止算法出 bug 时把用户手机炸掉——
    真撞到上限，说明有别的东西坏了。
    """
    daily = state.setdefault("daily", {})
    if daily.get("date") != today:          # 换天了，清零重来
        daily.clear()
        daily.update({"date": today, "total": 0, "by_ticker": {}})
    by_ticker = daily.setdefault("by_ticker", {})
    if daily.get("total", 0) >= alerts.MAX_PER_DAY:
        return False
    if by_ticker.get(ticker, 0) >= alerts.MAX_PER_TICKER_PER_DAY:
        return False
    daily["total"] = daily.get("total", 0) + 1
    by_ticker[ticker] = by_ticker.get(ticker, 0) + 1
    return True


def load_state(path: str) -> dict:
    """读去重状态。文件不在或坏了都返回空状态——大不了重新攒，
    绝不能因为一个 JSON 坏了就让进程起不来。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return empty_state()
    base = empty_state()
    if isinstance(data, dict):
        for key in base:
            if isinstance(data.get(key), dict):
                base[key] = data[key]
    return base


def save_state(path: str, state: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=1)
    except Exception:
        pass          # 存不下就算了，下一轮再试，不值得中断盯盘


def load_positions() -> list[dict]:
    """当前持仓。**每轮都重新读**，不缓存。"""
    return pg.query(
        "select ticker, market, avg_cost, shares, currency "
        "from user_positions order by market, ticker")


def load_bars(ticker: str, market: str, limit: int = BARS_LIMIT) -> list[dict]:
    """一只票最近的日线，**正序**返回（最后一根最新）。

    按票 `order by date desc limit N` 走索引扫描。绝不能写成
    `date = (select max(date) ...)` 那种关联子查询——实测是 275 万行
    顺序扫描，约 6.7 秒。
    """
    rows = pg.query(
        "select date, open, high, low, close, volume "
        "from stock_ohlcv_daily "
        "where ticker = %s and market = %s "
        "order by date desc limit %s",
        (ticker, market, int(limit)))
    return list(reversed(rows or []))


def avg_volume_of(bars: list[dict], days: int = 20) -> float:
    """最近 N 根日线的平均成交量，给盘中量比当分母。"""
    vols = [float(b["volume"]) for b in bars[-days:]
            if b.get("volume") is not None]
    return sum(vols) / len(vols) if vols else 0.0


def elapsed_minutes(now_utc: datetime.datetime, session_name: str) -> float:
    """这个时段已经开了多少分钟。A股的午休不算在内。"""
    if session_name == "cn":
        now = now_utc.astimezone(session_mod.CN_TZ)
        minutes = now.hour * 60 + now.minute
        total = 0.0
        for start, end in session_mod.CN_SPANS:
            if minutes >= end:
                total += end - start
            elif minutes > start:
                total += minutes - start
        return total
    if session_name in session_mod.US_SPANS:
        tz = session_mod._us_tz(now_utc.date())
        now = now_utc.astimezone(tz)
        minutes = now.hour * 60 + now.minute
        return max(0.0, minutes - session_mod.US_SPANS[session_name][0])
    return 0.0


def _levels_for(ticker: str, market: str, today: str) -> dict:
    """关键位，一天算一次，缓存在内存里。"""
    key = "%s|%s|%s" % (ticker, market, today)
    if key not in _levels_cache:
        if _levels_cache and not any(k.endswith("|" + today)
                                     for k in _levels_cache):
            _levels_cache.clear()          # 换天了，旧的全丢掉
        bars = load_bars(ticker, market)
        _levels_cache[key] = {
            "levels": levels.compute(bars, config.WATCHDOG_WARN_DIST),
            "avg_volume": avg_volume_of(bars),
        }
    return _levels_cache[key]


def _us_session_of(quote: dict, active: list[str]) -> str:
    """美股这一刻算哪个时段。优先信 UW 分钟线自带的 market_time
    （r/pr/po），它比按时间猜准——盘前盘后的边界在数据里是现成的。"""
    mapping = {"pr": "us_pre", "r": "us_rth", "po": "us_post"}
    tag = quote.get("session")
    if tag in mapping:
        return mapping[tag]
    for name in ("us_rth", "us_pre", "us_post"):
        if name in active:
            return name
    return "us_rth"


def run_once(state: dict, now_utc: datetime.datetime | None = None) -> list[dict]:
    """跑一轮。返回这一轮推出去的报警。任何一路失败都只影响那一路。"""
    now_utc = now_utc or datetime.datetime.now(UTC)
    active = session_mod.current(now_utc)
    if not config.WATCHDOG_PREMARKET:
        active = [s for s in active if s != "us_pre"]
    if not active:
        return []

    try:
        positions = load_positions() or []
    except Exception:
        return []
    if not positions:
        return []

    us_open = any(s.startswith("us_") for s in active)
    by_market = {}
    for row in positions:
        market = (row.get("market") or "US").upper()
        if market == "CN" and "cn" not in active:
            continue
        if market == "US" and not us_open:
            continue
        by_market.setdefault(market, []).append(row["ticker"])
    if not by_market:
        return []

    try:
        quote_map = quotes.get(by_market)
    except Exception:
        return []
    if not quote_map:
        return []

    today = now_utc.astimezone(session_mod.CN_TZ).date().isoformat()
    pos_by_ticker = {r["ticker"]: r for r in positions}
    fired = []

    for ticker, quote in quote_map.items():
        row = pos_by_ticker.get(ticker)
        if not row:
            continue
        market = (row.get("market") or "US").upper()
        name = "cn" if market == "CN" else _us_session_of(quote, active)
        try:
            cached = _levels_for(ticker, market, today)
        except Exception:
            continue
        got = alerts.evaluate(
            ticker, quote, cached["levels"], state, name,
            cached["avg_volume"], elapsed_minutes(now_utc, name), today)
        for alert in got:
            if not _within_daily_cap(state, ticker, today):
                continue          # 撞到保险丝，今天这只票不再打扰
            notify.send(notify.format_alert(alert, row))
            fired.append(alert)

    return fired


def main() -> None:
    parser = argparse.ArgumentParser(description="盯盘守护进程")
    parser.add_argument("--once", action="store_true",
                        help="只跑一轮就退出，调试用")
    args = parser.parse_args()

    if not notify.available():
        sys.stderr.write(
            "没配 TG_BOT_TOKEN / TG_CHAT_ID，盯盘起来也发不出消息。\n"
            "把它们填进 .env 再跑——一个「以为在盯盘、其实什么都没发」\n"
            "的进程比没有更糟。\n")
        raise SystemExit(1)

    path = config.WATCHDOG_STATE_FILE
    state = load_state(path)

    if args.once:
        got = run_once(state)
        save_state(path, state)
        print("推了 %d 条" % len(got))
        return

    print("盯盘启动，每 %d 秒一轮。Ctrl-C 停止。" % config.WATCHDOG_INTERVAL)
    while True:
        try:
            got = run_once(state)
            if got:
                save_state(path, state)
                for alert in got:
                    print("[%s] %s %s %.2f" % (
                        datetime.datetime.now().strftime("%H:%M:%S"),
                        alert["ticker"], alert["kind"], alert["price"]))
        except KeyboardInterrupt:
            print("停止。")
            save_state(path, state)
            return
        except Exception as exc:      # 任何没想到的错都不能让进程死
            sys.stderr.write("这一轮出错，跳过：%r\n" % (exc,))
        time.sleep(config.WATCHDOG_INTERVAL)


if __name__ == "__main__":
    main()
