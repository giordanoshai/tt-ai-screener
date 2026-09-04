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

服务器上由**用户级 systemd** 托管（`scripts/tt-watchdog.service`）：
崩了自动拉起、机器重启自动起来、日志进 journal 自动轮转。
每一轮的摘要写在 stderr，看日志用
`journalctl --user -u tt-watchdog -f`。
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

#: 某个市场连续多少轮一只报价都拿不到，就推一条 Telegram 告警。
#: 60 秒一轮，10 轮 = 10 分钟：足够跳过一次网络抖动，又不至于让
#: 「进程还在、其实什么都没在看」这个状态闷上半天没人知道。
QUOTE_FAIL_ALERT_ROUNDS = 10


def empty_state() -> dict:
    # daily 形如 {"date": "2026-09-04", "total": 3, "by_ticker": {"票": 2}}，
    # 换天自动清零。它是保险丝，见 _within_daily_cap。
    # quote_fail 形如 {"CN": 3}：某个市场连续几轮没拿到报价，见
    # _track_quote_failures。落地存是有意的——进程被 systemd 拉起重来
    # 之后，如果 AKTools 还是没好，不该从头再数 10 轮。
    return {"fired": {}, "warned": {}, "prev_last": {}, "ticks": {},
            "daily": {}, "quote_fail": {}}


def _log(now_utc: datetime.datetime, text: str) -> None:
    """往 stderr 写一行带时间戳的摘要（北京时间）。systemd 自动收进 journal。

    盯盘最典型的故障不是崩溃，是「进程还活着、但什么都没在看」——
    光看进程在不在完全分辨不出来。所以每一轮都留一行：哪个时段、
    读到几只持仓、拿到几条报价、推了几条。出事时能直接看出断在哪一步。
    """
    stamp = now_utc.astimezone(session_mod.CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    sys.stderr.write("[%s] %s\n" % (stamp, text))


def _market_label(market: str) -> str:
    return "A股" if (market or "").upper() == "CN" else "美股"


def _today_for(market: str, now_utc: datetime.datetime) -> str:
    """这一刻对这个市场来说算「哪一天」。A股按北京日期，美股按**美东日期**。

    两个市场不能都用北京日期：北京零点正好是美东中午 12:00（夏令时），
    落在美股常规时段的正中间。用北京日期的话，每天盘中会发生两件事——
    每日配额闸原地清零（一只美股上午 4 条、下午再 4 条，实际上限是设计
    说的两倍），关键位缓存也被清空重算，每只美股持仓白白多跑一遍日线查询。
    """
    if (market or "").upper() == "CN":
        return now_utc.astimezone(session_mod.CN_TZ).date().isoformat()
    return now_utc.astimezone(
        session_mod._us_tz(now_utc.date())).date().isoformat()


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


def _track_quote_failures(state: dict, wanted: dict, quote_map: dict,
                          now_utc: datetime.datetime) -> None:
    """数每个市场连续多少轮一只报价都没拿到，到阈值推一条 Telegram。

    这是「进程还在、其实什么都没在看」这个失败模式的唯一出口：取价失败
    本身是静默的（取不到就返回空），不主动告警的话，AKTools 慢了几秒、
    UW 换了鉴权，用户是不会知道的——他只会以为今天恰好没什么行情。

    推完就清零，避免每轮刷一条。
    """
    counters = state.setdefault("quote_fail", {})
    for market, tickers in wanted.items():
        if any(t in quote_map for t in tickers):
            counters.pop(market, None)          # 拿到了，从头再数
            continue
        counters[market] = counters.get(market, 0) + 1
        if counters[market] < QUOTE_FAIL_ALERT_ROUNDS:
            continue
        counters[market] = 0
        label = _market_label(market)
        _log(now_utc, "%s 连续 %d 轮一只报价都没拿到，已告警"
             % (label, QUOTE_FAIL_ALERT_ROUNDS))
        notify.send(notify.title(
            "🔴", "%s 连续 %d 轮一只报价都拿不到" % (
                label, QUOTE_FAIL_ALERT_ROUNDS))
            + "\n进程还活着，但这个市场实际上已经没在盯了。")


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
    except Exception as exc:
        # 读不到持仓这一轮就等于什么都没盯。以前这里是静默 return，
        # 数据库连不上时外面看不出任何异常。
        _log(now_utc, "读持仓失败，这一轮跳过：%r" % (exc,))
        return []
    if not positions:
        _log(now_utc, "%s | 没有持仓，无事可做" % "+".join(active))
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
        _log(now_utc, "%s | 持仓里没有这个时段该盯的票" % "+".join(active))
        return []

    try:
        quote_map = quotes.get(by_market) or {}
    except Exception as exc:
        # quotes.get 承诺不抛（每一路自己吞了异常），这是防御性的一层。
        _log(now_utc, "取价失败，这一轮跳过：%r" % (exc,))
        quote_map = {}

    # 连续多轮取不到报价要告警——静默失败正是这个功能最危险的失败方式。
    _track_quote_failures(state, by_market, quote_map, now_utc)
    if not quote_map:
        _log(now_utc, "%s | 持仓 %d | 报价 0 | 推送 0"
             % ("+".join(active), len(positions)))
        return []

    pos_by_ticker = {r["ticker"]: r for r in positions}
    fired = []

    for ticker, quote in quote_map.items():
        row = pos_by_ticker.get(ticker)
        if not row:
            continue
        market = (row.get("market") or "US").upper()
        # 「今天」按市场分别算：美股用美东日期，A股用北京日期。见 _today_for。
        today = _today_for(market, now_utc)
        name = "cn" if market == "CN" else _us_session_of(quote, active)
        try:
            cached = _levels_for(ticker, market, today)
        except Exception as exc:
            # 算不出关键位，这只票这一轮就没有可比的坎。以前是静默 continue，
            # 于是「某只票的日线查询一直在失败」可以一整天没人发现。
            _log(now_utc, "%s 算不出关键位，跳过：%r" % (ticker, exc))
            continue
        got = alerts.evaluate(
            ticker, quote, cached["levels"], state, name,
            cached["avg_volume"], elapsed_minutes(now_utc, name), today)
        for alert in got:
            if not _within_daily_cap(state, ticker, today):
                continue          # 撞到保险丝，今天这只票不再打扰
            ok = notify.send(notify.format_alert(alert, row, today))
            if not ok:
                # 推送真的失败了（代理挂了、token 失效……）。这时候
                # alerts.evaluate() 已经把这条报警计入冷却、
                # _within_daily_cap() 也已经把配额记掉了——如果就这么
                # 算了，就变成「已经进了 5 个交易日冷却、占了今日配额，
                # 但用户手机上什么都没有」，恰好是 config.py 里点名要
                # 避免的「以为在盯盘、其实什么都没发」。所以推送失败
                # 必须把这两笔记录都撤销。
                #
                # 撤销的是冷却和配额，**不是确认计数**：core/alerts.py
                # 在 fire 的那一刻已经把 ticks 清零了，这里也不去恢复它。
                # 所以实际行为不是「下一轮立刻重试」，而是价格得再连续
                # 命中 2 次（盘前 3 次）攒够确认才会重推，也就是 2~3 分钟
                # 之后。这是可以接受的：推送失败通常是代理或网络的问题，
                # 隔两分钟再来一次比原地猛敲更合适；而该重试的确实重试了。
                #
                # key 的拼法必须跟 core/alerts.py 的 _key() 保持一致
                # （"%s|%.2f" % (ticker, price)）——那是私有函数，
                # 不 import，这里按同样格式在本地拼一次。
                key = "%s|%.2f" % (ticker, alert["price"])
                bucket = "warned" if alert["kind"] == "接近预警" else "fired"
                state.setdefault(bucket, {}).pop(key, None)
                daily = state.setdefault("daily", {})
                if daily.get("total", 0) > 0:
                    daily["total"] -= 1
                by_ticker = daily.setdefault("by_ticker", {})
                if by_ticker.get(ticker, 0) > 0:
                    by_ticker[ticker] -= 1
                sys.stderr.write(
                    "推送失败，已撤销冷却和配额，下一轮重试：%s %s %.2f\n" % (
                        ticker, alert["kind"], alert["price"]))
                continue          # 失败的不进 fired，避免主循环把它当成功打印
            fired.append(alert)

    _log(now_utc, "%s | 持仓 %d | 报价 %d | 推送 %d" % (
        "+".join(active), len(positions), len(quote_map), len(fired)))
    return fired


def startup_notice() -> str:
    """启动时推的那条消息：让用户立刻知道它活着、而且盯的是哪几只。

    没有这条的话，「进程在跑」和「进程真的在盯」用户分不出来——
    而这个功能最典型的故障恰恰是后者悄悄没了。
    """
    try:
        positions = load_positions() or []
    except Exception as exc:
        return notify.title("🟡", "已启动，但读不到持仓：%r" % (exc,))
    cn = sum(1 for r in positions
             if (r.get("market") or "US").upper() == "CN")
    return notify.title("🟢", "已启动，当前盯 %d 只（A股 %d / 美股 %d）" % (
        len(positions), cn, len(positions) - cn))


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
        # --once 是调试用的干跑，不推启动通知——调一次代码就给用户手机
        # 发一条，是另一种刷屏。
        got = run_once(state)
        save_state(path, state)
        print("推了 %d 条" % len(got))
        return

    print("盯盘启动，每 %d 秒一轮。Ctrl-C 停止。" % config.WATCHDOG_INTERVAL)
    # 启动就报到。用户看到这条才知道「它活着、盯着这几只」，
    # 而不是只能去服务器上看进程还在不在。
    notify.send(startup_notice())
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
