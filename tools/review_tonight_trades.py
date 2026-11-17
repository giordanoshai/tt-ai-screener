# -*- coding: utf-8 -*-
"""工具：复盘今晚（美东当日）Firstrade (FT) 交易流水与明细。

数据来源为 `ftt_spread_trades`（垂直价差）与 `ftt_trades`（单腿期权），
按美国东部时间 (`America/New_York`) 的交易日窗口过滤：
`[00:00:00 ET, 次日 00:00:00 ET)`，自动兼容夏令时 (EDT) 与冬令时 (EST)。

返回体刻意精简（参考 `tools/compact.py` 原则）：
- 公共口径（`account="FT"`、`tz="America/New_York"`、价格单位）只放在 `summary`，
  不在每笔明细里重复。
- 每笔明细对当日单只保留 `HH:MM:SS`（跨夜单保留完整日期时间），`self_check`、
  `prior_day`、`partial_close` 等字段仅在非空 / 为真时附带。
- `entry` / `exit` 为美股期权单股价格；`pnl` 为已折算 100 股乘数的美元净盈亏。
"""
from collections import defaultdict
from datetime import date as date_cls, datetime, time, timedelta, timezone
import json
from zoneinfo import ZoneInfo

from db import pg

ET_TZ = ZoneInfo("America/New_York")

_SPREAD_SQL = """
select
    spread_id,
    ticker,
    direction,
    option_right,
    buy_code,
    sell_code,
    width,
    qty,
    entry_net,
    exit_net,
    entry_time,
    exit_time,
    status,
    realized_pnl,
    total_pnl,
    close_reason,
    template_snapshot,
    combo_info
from ftt_spread_trades
where (entry_time >= %s and entry_time < %s)
   or (exit_time  >= %s and exit_time  < %s)
order by entry_time asc, spread_id asc
"""

_SINGLE_SQL = """
select
    order_id,
    ticker,
    direction,
    option_code,
    qty,
    entry_mid,
    exit_mid,
    entry_time,
    exit_time,
    status,
    realized_pnl,
    total_pnl,
    close_reason,
    template_snapshot
from ftt_trades
where (entry_time >= %s and entry_time < %s)
   or (exit_time  >= %s and exit_time  < %s)
order by entry_time asc, order_id asc
"""


def _et_day_bounds(date_str: str | None) -> tuple[date_cls, datetime, datetime]:
    """解析目标美东日期并返回 (target_date, start_utc, end_utc)。"""
    if date_str:
        target = date_cls.fromisoformat(date_str.strip())
    else:
        target = datetime.now(ET_TZ).date()
    start_et = datetime.combine(target, time.min, tzinfo=ET_TZ)
    end_et = start_et + timedelta(days=1)
    return target, start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


def _fmt_et(dt: datetime | None, target_date: date_cls) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    et_dt = dt.astimezone(ET_TZ)
    if et_dt.date() == target_date:
        return et_dt.strftime("%H:%M:%S")
    return et_dt.strftime("%Y-%m-%d %H:%M:%S")


def _hold_minutes(entry_dt: datetime | None, exit_dt: datetime | None) -> float | None:
    if entry_dt is None or exit_dt is None:
        return None
    secs = max(0.0, (exit_dt - entry_dt).total_seconds())
    return round(secs / 60.0, 1)


def _round_opt(val, digits: int = 2) -> float | None:
    if val is None:
        return None
    return round(float(val), digits)


def _compact_leg_prices(raw_legs) -> dict[str, float | None]:
    """把开/平仓各腿压缩为 {side: fill_price}，避免重复 18 字符 OCC 代码。"""
    out: dict[str, float | None] = {}
    for idx, leg in enumerate(raw_legs or [], 1):
        if not isinstance(leg, dict):
            continue
        side = str(leg.get("side") or f"L{idx}").upper()
        key = side if side not in out else f"{side}_{idx}"
        fill = leg.get("fill_price")
        out[key] = round(float(fill), 4) if fill not in (None, "") else None
    return out


def _is_partial_close_mismatch(legs, close_legs, qty: int, total_pnl: float | None) -> bool:
    """检查分批平仓是否导致 combo_info.close_legs 仅保留最后一批单价。"""
    if total_pnl is None or qty <= 0 or not legs or not close_legs:
        return False
    cashflow = 0.0
    for leg in legs:
        p = float(leg.get("fill_price") or 0.0) * 100.0 * qty
        side = str(leg.get("side") or "").upper()
        cashflow += -p if side in ("BO", "BUY") else p
    for leg in close_legs:
        p = float(leg.get("fill_price") or 0.0) * 100.0 * qty
        side = str(leg.get("side") or "").upper()
        cashflow += p if side in ("SC", "SELL") else -p
    return abs(round(float(total_pnl) - cashflow, 4)) >= 0.01


def _pnl_pct(entry_price: float | None, qty: int, total_pnl: float | None) -> float | None:
    if total_pnl is None or entry_price is None or entry_price <= 0 or qty <= 0:
        return None
    cost = float(entry_price) * qty * 100.0
    if cost <= 0:
        return None
    return round(float(total_pnl) / cost * 100.0, 2)


def _base_trade_fields(row: dict, trade_id: str, kind: str,
                       entry_price, exit_price, target_date: date_cls) -> dict:
    entry_dt = row.get("entry_time")
    exit_dt = row.get("exit_time")
    qty = int(row.get("qty") or 0)
    pnl = row.get("total_pnl")
    if pnl is None:
        pnl = row.get("realized_pnl")

    tpl = row.get("template_snapshot") if isinstance(row.get("template_snapshot"), dict) else {}
    entry_et_date = entry_dt.astimezone(ET_TZ).date() if entry_dt else None

    item = {
        "id": trade_id,
        "kind": kind,
        "ticker": str(row.get("ticker") or "").upper(),
        "dir": row.get("direction"),
        "status": row.get("status"),
        "qty": qty,
        "entry": _round_opt(entry_price, 4),
        "exit": _round_opt(exit_price, 4),
        "pnl": _round_opt(pnl, 2),
        "pnl_pct": _pnl_pct(entry_price, qty, pnl),
        "in_et": _fmt_et(entry_dt, target_date),
        "out_et": _fmt_et(exit_dt, target_date),
        "hold_m": _hold_minutes(entry_dt, exit_dt),
        "reason": row.get("close_reason"),
        "tpl": tpl.get("name"),
        "_sort_ts": entry_dt.isoformat() if entry_dt else "",
    }
    if entry_et_date and entry_et_date != target_date:
        item["prior_day"] = True
    if tpl.get("self_check"):
        item["self_check"] = tpl.get("self_check")
    return item


def _normalize_spread(row: dict, target_date: date_cls) -> dict:
    item = _base_trade_fields(
        row,
        trade_id=str(row.get("spread_id") or ""),
        kind="spread",
        entry_price=row.get("entry_net"),
        exit_price=row.get("exit_net"),
        target_date=target_date,
    )
    combo = row.get("combo_info") if isinstance(row.get("combo_info"), dict) else {}
    raw_legs = combo.get("legs") or []
    raw_close_legs = combo.get("close_legs") or []
    item.update({
        "right": row.get("option_right"),
        "buy": row.get("buy_code"),
        "sell": row.get("sell_code"),
        "width": row.get("width"),
    })
    legs_in = _compact_leg_prices(raw_legs)
    legs_out = _compact_leg_prices(raw_close_legs)
    if legs_in:
        item["legs_in"] = legs_in
    if legs_out:
        item["legs_out"] = legs_out
    if _is_partial_close_mismatch(raw_legs, raw_close_legs, item["qty"], item["pnl"]):
        item["partial_close"] = True
    return item


def _normalize_single(row: dict, target_date: date_cls) -> dict:
    item = _base_trade_fields(
        row,
        trade_id=str(row.get("order_id") or ""),
        kind="single",
        entry_price=row.get("entry_mid"),
        exit_price=row.get("exit_mid"),
        target_date=target_date,
    )
    item["option_code"] = row.get("option_code")
    return item


def _summarize(all_trades: list[dict], target_date: date_cls,
               cancelled_count: int) -> dict:
    real_trades = [t for t in all_trades if t["status"] != "cancelled"]
    closed = [t for t in real_trades if t["status"] == "closed"]
    open_trades = [t for t in real_trades if t["status"] == "open"]

    pnls = [t["pnl"] for t in closed if t["pnl"] is not None]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    even = sum(1 for p in pnls if p == 0)
    pnl_usd = round(sum(pnls), 2)
    win_rate = round(wins / len(pnls) * 100.0, 1) if pnls else None

    holds = [t["hold_m"] for t in closed if t["hold_m"] is not None]
    avg_hold = round(sum(holds) / len(holds), 1) if holds else None

    pnl_closed = [t for t in closed if t["pnl"] is not None]
    best = max(pnl_closed, key=lambda t: t["pnl"], default=None)
    worst = min(pnl_closed, key=lambda t: t["pnl"], default=None)

    ticker_buckets = defaultdict(lambda: {"n": 0, "w": 0, "l": 0, "pnl": 0.0})
    reason_counts = defaultdict(int)
    for t in closed:
        b = ticker_buckets[t["ticker"]]
        b["n"] += 1
        p = t["pnl"]
        if p is not None:
            b["pnl"] += p
            if p > 0:
                b["w"] += 1
            elif p < 0:
                b["l"] += 1
        reason_counts[t.get("reason") or "未标注"] += 1

    by_ticker = [
        {"ticker": sym, "n": v["n"], "w": v["w"], "l": v["l"], "pnl": round(v["pnl"], 2)}
        for sym, v in sorted(ticker_buckets.items(), key=lambda kv: (-kv[1]["pnl"], kv[0]))
    ]

    date_str = target_date.isoformat()
    if not real_trades and cancelled_count == 0:
        note = f"No FT trades recorded for {date_str} (ET)."
    elif not real_trades:
        note = f"No filled FT trades for {date_str} (ET); {cancelled_count} cancelled."
    else:
        note = (
            f"{len(closed)} closed, {len(open_trades)} open, {cancelled_count} cancelled; "
            f"net PnL ${pnl_usd:+.2f}."
        )

    return {
        "date_et": date_str,
        "tz": "America/New_York",
        "account": "FT",
        "unit": "entry/exit per share (x100/contract); pnl in USD",
        "count": len(real_trades),
        "closed": len(closed),
        "open": len(open_trades),
        "cancelled": cancelled_count,
        "wins": wins,
        "losses": losses,
        "even": even,
        "win_rate_pct": win_rate,
        "pnl_usd": pnl_usd,
        "avg_hold_m": avg_hold,
        "best": {"id": best["id"], "ticker": best["ticker"], "pnl": best["pnl"]} if best else None,
        "worst": {"id": worst["id"], "ticker": worst["ticker"], "pnl": worst["pnl"]} if worst else None,
        "by_ticker": by_ticker,
        "by_reason": dict(reason_counts),
        "note": note,
    }


def review_tonight_trades(date: str | None = None,
                          include_cancelled: bool = False) -> str:
    """获取美东当日（或指定日期）Firstrade (FT) 交易流水与明细。

    从 `ftt_spread_trades`（垂直价差）和 `ftt_trades`（单腿期权）查询美东时间
    当日开仓或平仓的交易记录，返回汇总统计与精简逐笔明细。

    参数:
        date: 可选，美东目标日期 `YYYY-MM-DD`，默认取当前美东日期。
        include_cancelled: 是否在 `trades` 列表中包含未成交撤单（`status='cancelled'`），默认 `False`。
    """
    try:
        target_date, start_utc, end_utc = _et_day_bounds(date)
    except ValueError:
        return json.dumps({"error": f"Invalid date format: {date!r}. Expected YYYY-MM-DD."},
                          ensure_ascii=False)

    params = (start_utc, end_utc, start_utc, end_utc)
    all_trades: list[dict] = []
    data_gaps: list[str] = []

    try:
        spread_rows = pg.query(_SPREAD_SQL, params)
        all_trades.extend(_normalize_spread(r, target_date) for r in spread_rows)
    except Exception as exc:
        data_gaps.append(f"ftt_spread_trades 查询失败: {exc}")

    try:
        single_rows = pg.query(_SINGLE_SQL, params)
        all_trades.extend(_normalize_single(r, target_date) for r in single_rows)
    except Exception as exc:
        data_gaps.append(f"ftt_trades 查询失败: {exc}")

    all_trades.sort(key=lambda t: (t.pop("_sort_ts", ""), t["id"]))
    cancelled_count = sum(1 for t in all_trades if t["status"] == "cancelled")

    visible = all_trades if include_cancelled else [
        t for t in all_trades if t["status"] != "cancelled"
    ]

    return json.dumps({
        "summary": _summarize(all_trades, target_date, cancelled_count),
        "trades": visible,
        "data_gaps": data_gaps,
    }, ensure_ascii=False)
