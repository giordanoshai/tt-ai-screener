# -*- coding: utf-8 -*-
"""交易复盘与期权流水工具（由 TTAiTradingSystem 迁入）。

包含 `trades` / `executions` / `strategy_templates` / `strategy_signals` /
`option_ohlcv_1min` / `option_market_snapshots` 的只读查询，统一走 `db.pg`。

`trades` 表中垂直/日历价差以 `metrics.combo_info.parent_order_id` 成对存储为两条腿，
本模块在查询侧自动识别并聚合双腿（同时兼容历史单行斜杠格式），返回精简后的复盘视图。
"""
from datetime import datetime
import html
import re
import uuid

from db import pg
from tools._common import (
    calc_mfe_capture_rate,
    dump_json,
    normalize_direction,
    short_option_contract,
    short_spread_contract,
)

_PG_INT4_MAX = 2_147_483_647

_TRADE_SELECT_SQL = """
    select
        id, display_id, ticker, direction, option_code, status,
        entry_price, exit_price, total_qty, remain_qty,
        total_pnl, total_pnl_percent, total_commission,
        metrics, review_note, entry_time, exit_time,
        r_multiple, hold_duration_minutes, ideal_plan,
        daily_market_snapshot, would_take_again, trade_type,
        broker_account, self_check_reviewed, psychology_score
    from trades
"""


def _clean_note(raw: str | None, max_len: int | None = None) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", "", raw)
    text = html.unescape(text)
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned[:max_len] if max_len else cleaned


def _format_minute(val) -> str:
    if not val:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M")
    s = str(val).replace("T", " ").split("+", 1)[0]
    return s[:16] if len(s) >= 16 else s


def _format_ts(val) -> str | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    s = str(val).replace("T", " ").split("+", 1)[0].split(".", 1)[0]
    return s[:19] if len(s) >= 19 else s


def _to_float(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _round_opt(val, digits: int = 2) -> float | None:
    num = _to_float(val)
    return round(num, digits) if num is not None else None


def _compact_dict(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None and v != {} and v != []}


def _filter_deviation_flags(raw_flags) -> list[str]:
    out: list[str] = []
    for f in raw_flags or []:
        if f and f != "NO_DEVIATION" and f not in out:
            out.append(f)
    return out


def _build_trade_where(
    status: str | None,
    start_date: str | None,
    end_date: str | None,
    trade_type: str | None,
    ticker: str | None,
    direction: str | None,
) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []

    if status:
        clauses.append("upper(status) = upper(%s)")
        params.append(status.strip())
    if trade_type:
        clauses.append("upper(trade_type) = upper(%s)")
        params.append(trade_type.strip())
    if ticker:
        clauses.append("upper(ticker) = %s")
        params.append(ticker.strip().upper())
    norm_dir = normalize_direction(direction)
    if norm_dir:
        clauses.append(
            "(case when metrics->'combo_info' is not null "
            "then upper(coalesce(ideal_plan->>'strategy_direction_view', direction)) "
            "else upper(direction) end) = %s"
        )
        params.append(norm_dir)
    if start_date:
        clauses.append("entry_time >= %s")
        params.append(start_date.strip())
    if end_date:
        clauses.append("entry_time <= %s")
        params.append(f"{end_date.strip()}T23:59:59Z")

    where_sql = f"where {' and '.join(clauses)}" if clauses else ""
    return where_sql, params


def _combo_info(row: dict) -> dict:
    m = row.get("metrics")
    if isinstance(m, dict) and isinstance(m.get("combo_info"), dict):
        return m["combo_info"]
    return {}


def _split_combo_legs(legs: list[dict]) -> tuple[dict, dict]:
    """区分双腿组合中的买腿 (LONG_ASSET) 与卖腿 (SHORT_ASSET)。"""
    def _is_buy_leg(row: dict) -> bool:
        plan = row.get("ideal_plan") or {}
        if plan.get("asset_logic") == "LONG_ASSET":
            return True
        if plan.get("asset_logic") == "SHORT_ASSET":
            return False
        return str(row.get("direction") or "").upper() == "LONG"

    ordered = sorted(
        legs,
        key=lambda r: (
            int(_combo_info(r).get("leg_index") or 99),
            int(r.get("display_id") or 0),
        ),
    )
    if len(ordered) >= 2:
        if _is_buy_leg(ordered[0]) and not _is_buy_leg(ordered[1]):
            return ordered[0], ordered[1]
        if _is_buy_leg(ordered[1]) and not _is_buy_leg(ordered[0]):
            return ordered[1], ordered[0]
        return ordered[0], ordered[1]
    return ordered[0], ordered[0]


def _leg_entry(row: dict, side: str) -> dict:
    return _compact_dict({
        "id": row.get("display_id"),
        "side": side,
        "code": row.get("option_code"),
        "entry": _round_opt(row.get("entry_price"), 2),
        "exit": _round_opt(row.get("exit_price"), 2),
        "pnl": _round_opt(row.get("total_pnl"), 2),
    })


def _extract_plan(ideal_plan: dict | None) -> dict:
    if not isinstance(ideal_plan, dict):
        return {}
    rs = ideal_plan.get("risk_settings") or {}
    return _compact_dict({
        "template": ideal_plan.get("template_name"),
        "stop": _round_opt(ideal_plan.get("planned_stop"), 2),
        "target": _round_opt(ideal_plan.get("planned_target"), 2),
        "stop_pct": _to_float(rs.get("stop_percent")),
    })


def _extract_market(snapshot: dict | None) -> dict:
    if not isinstance(snapshot, dict):
        return {}
    spy = snapshot.get("spy_ohlcv") or {}
    breadth = snapshot.get("market_breadth") or {}
    return _compact_dict({
        "date": snapshot.get("snapshot_date"),
        "regime": snapshot.get("regime"),
        "vix": _round_opt(snapshot.get("vix_close"), 2),
        "spy_chg_pct": _round_opt(spy.get("change_pct"), 2),
        "ad_ratio": _round_opt(breadth.get("advance_decline"), 2),
    })


def _merge_combo_rows(legs: list[dict], primary_display_id: int | None = None) -> dict:
    """将双腿记录（或单条孤立组合腿）合并为统一逻辑交易字典。"""
    if len(legs) == 1:
        return _build_single_logical_trade(legs[0])

    buy_leg, sell_leg = _split_combo_legs(legs)
    buy_in = _to_float(buy_leg.get("entry_price"))
    sell_in = _to_float(sell_leg.get("entry_price"))
    buy_out = _to_float(buy_leg.get("exit_price"))
    sell_out = _to_float(sell_leg.get("exit_price"))

    entry_price = round(buy_in - sell_in, 2) if (buy_in is not None and sell_in is not None) else None
    exit_price = round(buy_out - sell_out, 2) if (buy_out is not None and sell_out is not None) else None

    buy_pnl = _to_float(buy_leg.get("total_pnl"))
    sell_pnl = _to_float(sell_leg.get("total_pnl"))
    total_pnl = round((buy_pnl or 0.0) + (sell_pnl or 0.0), 2) if (
        buy_pnl is not None or sell_pnl is not None
    ) else None

    qty = int(buy_leg.get("total_qty") or sell_leg.get("total_qty") or 0)
    total_pnl_pct = (
        round(total_pnl / (abs(entry_price) * qty), 4)
        if (total_pnl is not None and entry_price and qty > 0)
        else None
    )

    plan_raw = buy_leg.get("ideal_plan") or sell_leg.get("ideal_plan") or {}
    stop_pct = _to_float(((plan_raw.get("risk_settings") or {}).get("stop_percent"))) or 0.1
    if total_pnl is not None and entry_price and qty > 0 and stop_pct > 0:
        r_mult = round(total_pnl / (abs(entry_price) * qty * stop_pct), 2)
    else:
        r_mult = _round_opt(buy_leg.get("r_multiple"), 2)

    ticker = str(buy_leg.get("ticker") or sell_leg.get("ticker") or "").upper()
    direction = str(
        plan_raw.get("strategy_direction_view")
        or buy_leg.get("direction")
        or ""
    ).upper()
    combo_info = _combo_info(buy_leg) or _combo_info(sell_leg)
    combo_type = str(combo_info.get("combo_type") or "VERTICAL").upper()
    buy_code = buy_leg.get("option_code")
    sell_code = sell_leg.get("option_code")

    buy_metrics = buy_leg.get("metrics") if isinstance(buy_leg.get("metrics"), dict) else {}
    trade_metrics = _compact_dict({
        "mae_pct": _round_opt(buy_metrics.get("mae_percent"), 2),
        "mfe_pct": _round_opt(buy_metrics.get("mfe_percent"), 2),
    })

    primary_row = next(
        (r for r in legs if r.get("display_id") == primary_display_id),
        buy_leg,
    )

    return {
        "display_id": primary_row.get("display_id"),
        "kind": "spread",
        "combo_type": combo_type,
        "parent_order_id": combo_info.get("parent_order_id"),
        "ticker": ticker,
        "direction": direction,
        "contract": short_spread_contract(buy_code, sell_code, ticker),
        "option_code": f"{buy_code}/{sell_code}" if (buy_code and sell_code) else (buy_code or sell_code),
        "status": buy_leg.get("status") or sell_leg.get("status"),
        "trade_type": buy_leg.get("trade_type") or sell_leg.get("trade_type"),
        "broker_account": buy_leg.get("broker_account") or sell_leg.get("broker_account"),
        "leg_ids": [buy_leg.get("display_id"), sell_leg.get("display_id")],
        "legs": [_leg_entry(buy_leg, "BUY"), _leg_entry(sell_leg, "SELL")],
        "trade_ids": [str(buy_leg["id"]), str(sell_leg["id"])],
        "leg_id_map": {
            str(buy_leg["id"]): buy_leg.get("display_id"),
            str(sell_leg["id"]): sell_leg.get("display_id"),
        },
        "entry_price": entry_price,
        "exit_price": exit_price,
        "total_qty": qty,
        "total_pnl": total_pnl,
        "total_pnl_percent": total_pnl_pct,
        "r_multiple": r_mult,
        "entry_time_raw": buy_leg.get("entry_time") or sell_leg.get("entry_time"),
        "exit_time_raw": buy_leg.get("exit_time") or sell_leg.get("exit_time"),
        "hold_duration_minutes": buy_leg.get("hold_duration_minutes"),
        "plan": _extract_plan(plan_raw),
        "market_raw": buy_leg.get("daily_market_snapshot") or sell_leg.get("daily_market_snapshot"),
        "metrics": trade_metrics,
        "mfe_capture": None,
        "self_check_reviewed": buy_leg.get("self_check_reviewed") or sell_leg.get("self_check_reviewed"),
        "psychology_score": _round_opt(buy_leg.get("psychology_score"), 2),
        "would_take_again": buy_leg.get("would_take_again"),
        "review_note": buy_leg.get("review_note") or sell_leg.get("review_note"),
    }


def _build_single_logical_trade(row: dict) -> dict:
    code = row.get("option_code")
    ticker = str(row.get("ticker") or "").upper()
    is_hist_spread = bool(code and "/" in str(code))
    raw_metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    mfe_pct = _round_opt(raw_metrics.get("mfe_percent"), 2)
    capture = calc_mfe_capture_rate(
        _to_float(row.get("total_pnl_percent")),
        _to_float(raw_metrics.get("mfe_percent")),
        _to_float(row.get("total_pnl")),
    )
    capture_rounded = round(capture, 3) if capture is not None else None
    trade_metrics = _compact_dict({
        "mae_pct": _round_opt(raw_metrics.get("mae_percent"), 2),
        "mfe_pct": mfe_pct,
        "mfe_capture": capture_rounded,
    })

    out = {
        "display_id": row.get("display_id"),
        "kind": "spread" if is_hist_spread else "single",
        "combo_type": "VERTICAL" if is_hist_spread else None,
        "parent_order_id": None,
        "ticker": ticker,
        "direction": str(row.get("direction") or "").upper(),
        "contract": short_option_contract(code, ticker),
        "option_code": code,
        "status": row.get("status"),
        "trade_type": row.get("trade_type"),
        "broker_account": row.get("broker_account"),
        "leg_ids": None,
        "legs": None,
        "trade_ids": [str(row["id"])],
        "leg_id_map": {str(row["id"]): row.get("display_id")},
        "entry_price": _round_opt(row.get("entry_price"), 2),
        "exit_price": _round_opt(row.get("exit_price"), 2),
        "total_qty": row.get("total_qty"),
        "total_pnl": _round_opt(row.get("total_pnl"), 2),
        "total_pnl_percent": _round_opt(row.get("total_pnl_percent"), 4),
        "r_multiple": _round_opt(row.get("r_multiple"), 2),
        "entry_time_raw": row.get("entry_time"),
        "exit_time_raw": row.get("exit_time"),
        "hold_duration_minutes": row.get("hold_duration_minutes"),
        "plan": _extract_plan(row.get("ideal_plan")),
        "market_raw": row.get("daily_market_snapshot"),
        "metrics": trade_metrics,
        "mfe_capture": capture_rounded,
        "self_check_reviewed": row.get("self_check_reviewed"),
        "psychology_score": _round_opt(row.get("psychology_score"), 2),
        "would_take_again": row.get("would_take_again"),
        "review_note": row.get("review_note"),
    }
    return out


def _fetch_and_group_trades(
    limit: int,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    trade_type: str | None = None,
    ticker: str | None = None,
    direction: str | None = None,
) -> list[dict]:
    where_sql, params = _build_trade_where(
        status, start_date, end_date, trade_type, ticker, direction
    )
    fetch_limit = max(int(limit) * 2 + 6, 20)
    sql = f"""
        {_TRADE_SELECT_SQL}
        {where_sql}
        order by entry_time desc nulls last, display_id asc
        limit %s
    """
    rows = pg.query(sql, tuple(params) + (fetch_limit,))
    if not rows:
        return []

    rows_by_id = {str(r["id"]): r for r in rows}
    parent_buckets: dict[str, list[dict]] = {}
    for r in rows:
        pid = _combo_info(r).get("parent_order_id")
        if pid:
            parent_buckets.setdefault(pid, []).append(r)

    # 补齐因 LIMIT 截断而只拉到半边腿的组合单
    incomplete_pids = [pid for pid, legs in parent_buckets.items() if len(legs) < 2]
    if incomplete_pids:
        extra = pg.query(
            f"""
            {_TRADE_SELECT_SQL}
            where metrics->'combo_info'->>'parent_order_id' = any(%s)
            order by entry_time desc nulls last, display_id asc
            """,
            (incomplete_pids,),
        )
        for er in extra:
            sid = str(er["id"])
            if sid in rows_by_id:
                continue
            rows_by_id[sid] = er
            pid = _combo_info(er).get("parent_order_id")
            if pid:
                parent_buckets.setdefault(pid, []).append(er)

    grouped: list[dict] = []
    seen_pids: set[str] = set()
    for r in rows:
        pid = _combo_info(r).get("parent_order_id")
        if pid:
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            grouped.append(_merge_combo_rows(parent_buckets[pid]))
        else:
            grouped.append(_build_single_logical_trade(r))
        if len(grouped) >= int(limit):
            break

    return grouped


def _format_logical_trade(trade: dict, include_market: bool) -> dict:
    out: dict = {
        "display_id": trade["display_id"],
        "kind": trade["kind"],
    }
    if trade.get("combo_type"):
        out["combo_type"] = trade["combo_type"]
    out.update({
        "ticker": trade["ticker"],
        "direction": trade["direction"],
        "contract": trade["contract"],
        "option_code": trade["option_code"],
        "status": trade["status"],
        "trade_type": trade["trade_type"],
        "broker_account": trade["broker_account"],
    })
    if trade.get("leg_ids"):
        out["leg_ids"] = trade["leg_ids"]
    if trade.get("legs"):
        out["legs"] = trade["legs"]

    out.update({
        "entry_price": trade["entry_price"],
        "exit_price": trade["exit_price"],
        "total_qty": trade["total_qty"],
        "total_pnl": trade["total_pnl"],
        "total_pnl_percent": trade["total_pnl_percent"],
        "r_multiple": trade["r_multiple"],
        "entry_time": _format_ts(trade.get("entry_time_raw")),
        "exit_time": _format_ts(trade.get("exit_time_raw")),
        "hold_duration_minutes": trade["hold_duration_minutes"],
    })
    if trade.get("plan"):
        out["plan"] = trade["plan"]
    if include_market:
        mkt = _extract_market(trade.get("market_raw"))
        if mkt:
            out["market"] = mkt
    if trade.get("metrics"):
        out["metrics"] = trade["metrics"]

    sc = trade.get("self_check_reviewed")
    if isinstance(sc, dict):
        active = [k for k, v in sc.items() if v]
        if active:
            out["self_check"] = active

    if trade.get("psychology_score") is not None:
        out["psychology_score"] = trade["psychology_score"]
    if trade.get("would_take_again") is not None:
        out["would_take_again"] = trade["would_take_again"]

    note = _clean_note(trade.get("review_note"))
    if note:
        out["review_note"] = note
    return out


def get_recent_trades(
    limit: int = 10,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    trade_type: str | None = None,
    ticker: str | None = None,
    direction: str | None = None,
) -> str:
    """获取最近的交易记录 (trades)，自动合并垂直/日历价差双腿并精简字段。

    可选参数:
    - limit: 返回的最大逻辑交易笔数，默认 10
    - status: 'OPEN' / 'CLOSED' / 'PARTIAL'（大小写不敏感）
    - start_date, end_date: ISO 日期，如 '2026-05-01'
    - trade_type: 'PAPER' 或 'STRAND'
    - ticker: 标的代码，如 'AAPL'
    - direction: 'LONG'/'SHORT'（支持 'L'/'S'/'做多'/'做空' 别名）
    """
    try:
        grouped = _fetch_and_group_trades(
            limit, status, start_date, end_date, trade_type, ticker, direction
        )
        seen_dates: set[str] = set()
        result: list[dict] = []
        for t in grouped:
            eff = t.get("entry_time_raw")
            date_str = str(eff)[:10] if eff else ""
            include_market = bool(date_str) and date_str not in seen_dates
            formatted = _format_logical_trade(t, include_market=include_market)
            if include_market and "market" in formatted:
                seen_dates.add(date_str)
            result.append(formatted)
        return dump_json(result)
    except Exception as exc:
        return f"获取交易记录失败: {exc}"


def get_recent_trades_light(
    limit: int = 500,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    trade_type: str | None = None,
    ticker: str | None = None,
    direction: str | None = None,
) -> str:
    """以轻量化 Schema+Array 结构获取交易记录（自动合并价差双腿，含合约、偏差标签与体制）。"""
    try:
        grouped = _fetch_and_group_trades(
            limit, status, start_date, end_date, trade_type, ticker, direction
        )

        all_trade_ids: list[str] = []
        for t in grouped:
            all_trade_ids.extend(t["trade_ids"])

        flags_by_trade: dict[str, list[str]] = {}
        if all_trade_ids:
            ex_rows = pg.query(
                """
                select trade_id::text as trade_id, behavior_snapshot
                from executions
                where trade_id = any(%s::uuid[])
                order by transaction_time asc, id asc
                """,
                (all_trade_ids,),
            )
            for ex in ex_rows:
                tid = ex.get("trade_id")
                flags = _filter_deviation_flags(
                    (ex.get("behavior_snapshot") or {}).get("deviation_flags")
                )
                if not tid or not flags:
                    continue
                bucket = flags_by_trade.setdefault(tid, [])
                for f in flags:
                    if f not in bucket:
                        bucket.append(f)

        schema = [
            "display_id", "ticker", "dir", "kind", "contract",
            "entry", "exit", "qty", "pnl", "r",
            "entry_time", "hold_min", "mfe_capture",
            "deviation_flags", "regime", "would_again", "note",
        ]
        rows = []
        for t in grouped:
            d = str(t.get("direction") or "")
            dir_val = "L" if d == "LONG" else ("S" if d == "SHORT" else d[:1])
            kind_val = (t.get("combo_type") or t.get("kind") or "single").lower()

            merged_flags: list[str] = []
            for tid in t["trade_ids"]:
                for f in flags_by_trade.get(tid, []):
                    if f not in merged_flags:
                        merged_flags.append(f)

            snapshot = t.get("market_raw") or {}
            rows.append([
                t.get("display_id"),
                t.get("ticker"),
                dir_val,
                kind_val,
                t.get("contract"),
                t.get("entry_price"),
                t.get("exit_price"),
                t.get("total_qty"),
                t.get("total_pnl"),
                t.get("r_multiple"),
                _format_minute(t.get("entry_time_raw")),
                t.get("hold_duration_minutes"),
                t.get("mfe_capture"),
                merged_flags,
                snapshot.get("regime"),
                t.get("would_take_again"),
                _clean_note(t.get("review_note"), max_len=200),
            ])

        return dump_json({"schema": schema, "rows": rows})
    except Exception as exc:
        return f"获取轻量化交易记录失败: {exc}"


def _compact_market_snapshot(ms: dict | None) -> dict:
    if not isinstance(ms, dict):
        return {}
    m = ms.get("market") or {}
    t = ms.get("ticker") or {}
    o = ms.get("option") or {}
    ind = ms.get("indicators") or {}

    spy = _round_opt(m.get("spy_price"), 2)
    vix = _round_opt(m.get("vix"), 2)
    if vix == 0 and spy is None:
        vix = None
    market = _compact_dict({
        "spy": spy,
        "spy_chg_pct": _round_opt(m.get("spy_change_pct"), 2),
        "vix": vix,
    })

    vs_ma20 = t.get("price_vs_ma20")
    if vs_ma20 is None:
        vs_ma20 = ind.get("price_vs_ma20")
    ticker = _compact_dict({
        "price": _round_opt(t.get("price"), 2),
        "atr": _round_opt(t.get("atr"), 2),
        "vol_ratio": _round_opt(t.get("volume_ratio"), 2),
        "vs_ma20": _round_opt(vs_ma20, 2),
    })

    option = _compact_dict({
        "iv": _round_opt(o.get("iv"), 2),
        "delta": _round_opt(o.get("delta"), 3),
        "gamma": _round_opt(o.get("gamma"), 4),
        "theta": _round_opt(o.get("theta"), 3),
        "vega": _round_opt(o.get("vega"), 3),
        "dte": o.get("days_to_expiry"),
    })

    def _last_val(val, digits: int) -> float | None:
        if isinstance(val, list):
            val = val[-1] if val else None
        return _round_opt(val, digits)

    indicators = _compact_dict({
        "rsi_14": _last_val(ind.get("rsi_14"), 2),
        "macd_hist": _last_val(ind.get("macd_hist"), 4),
        "bb_pos": _round_opt(ind.get("bb_position"), 2),
    })

    return _compact_dict({
        "market": market,
        "ticker": ticker,
        "option": option,
        "indicators": indicators,
    })


def _compact_psychology(psy: dict | None) -> dict:
    if not isinstance(psy, dict):
        return {}
    return _compact_dict({
        "confidence": _round_opt(psy.get("confidence"), 2),
        "fear": _round_opt(psy.get("fear_level"), 2),
        "anxiety": _round_opt(psy.get("anxiety_score"), 2),
        "tension": _round_opt(psy.get("tension_score"), 2),
        "revenge": psy.get("revenge_trading"),
    })


def _compact_execution(ex: dict, leg_id_map: dict[str, int | None]) -> tuple[dict, list[str]]:
    flags = _filter_deviation_flags(
        (ex.get("behavior_snapshot") or {}).get("deviation_flags")
    )
    sp = ex.get("strategy_params") or {}
    strat_params = _compact_dict({
        "fast": _round_opt(sp.get("4in1_fast_exec"), 2),
        "slow": _round_opt(sp.get("4in1_slow_exec"), 2),
    })

    item = _compact_dict({
        "id": ex.get("id"),
        "leg_id": leg_id_map.get(str(ex.get("trade_id"))),
        "broker_order_id": ex.get("broker_order_id"),
        "side": ex.get("side"),
        "action_type": ex.get("action_type"),
        "qty": ex.get("qty"),
        "price": _round_opt(ex.get("price"), 2),
        "transaction_time": _format_ts(ex.get("transaction_time")),
        "strategy": ex.get("strategy"),
        "deviation_flags": flags,
        "strategy_params": strat_params,
        "market_snapshot": _compact_market_snapshot(ex.get("market_snapshot")),
        "psychology": _compact_psychology(ex.get("psychology")),
    })
    return item, flags


def get_trade_details(trade_id: str) -> str:
    """获取指定交易记录的详细信息（自动合并价差双腿，含精简后的 executions 与偏差标签）。

    trade_id 支持传入任意一腿的 display_id（数字）、UUID，或 parent_order_id（如 'COMBO-...'）。
    """
    try:
        tid_str = str(trade_id).strip()
        legs: list[dict] = []
        primary_display_id: int | None = None

        if tid_str.isdigit() and int(tid_str) <= _PG_INT4_MAX:
            row = pg.query_one(
                f"{_TRADE_SELECT_SQL} where display_id = %s",
                (int(tid_str),),
            )
            if row:
                primary_display_id = row.get("display_id")
                legs = [row]
        else:
            parsed_uuid: uuid.UUID | None = None
            try:
                parsed_uuid = uuid.UUID(tid_str)
            except ValueError:
                parsed_uuid = None

            if parsed_uuid is not None:
                row = pg.query_one(
                    f"{_TRADE_SELECT_SQL} where id = %s::uuid",
                    (str(parsed_uuid),),
                )
                if row:
                    primary_display_id = row.get("display_id")
                    legs = [row]
            else:
                legs = pg.query(
                    f"""
                    {_TRADE_SELECT_SQL}
                    where metrics->'combo_info'->>'parent_order_id' = %s
                    order by coalesce((metrics->'combo_info'->>'leg_index')::int, 99) asc,
                             display_id asc
                    """,
                    (tid_str,),
                )

        if not legs:
            return f"未找到交易: {trade_id}"

        if len(legs) == 1:
            pid = _combo_info(legs[0]).get("parent_order_id")
            if pid:
                legs = pg.query(
                    f"""
                    {_TRADE_SELECT_SQL}
                    where metrics->'combo_info'->>'parent_order_id' = %s
                    order by coalesce((metrics->'combo_info'->>'leg_index')::int, 99) asc,
                             display_id asc
                    """,
                    (pid,),
                )

        logical = _merge_combo_rows(legs, primary_display_id=primary_display_id)
        detail = _format_logical_trade(logical, include_market=True)

        executions = pg.query(
            """
            select id, trade_id, broker_order_id, side, action_type, qty, price,
                   transaction_time, market_snapshot,
                   strategy, strategy_params, psychology, behavior_snapshot
            from executions
            where trade_id = any(%s::uuid[])
            order by transaction_time asc, id asc
            """,
            (logical["trade_ids"],),
        )

        deviation_flags: list[str] = []
        compact_execs: list[dict] = []
        for ex in executions:
            ce, flags = _compact_execution(ex, logical["leg_id_map"])
            compact_execs.append(ce)
            for f in flags:
                if f not in deviation_flags:
                    deviation_flags.append(f)

        detail["deviation_flags"] = deviation_flags
        detail["executions"] = compact_execs
        return dump_json(detail)
    except Exception as exc:
        return f"获取交易详情失败: {exc}"


def get_strategy_templates(only_active: bool = True) -> str:
    """获取交易策略模板 (strategy_templates)。"""
    try:
        where_sql = "where is_active = true" if only_active else ""
        sql = f"""
            select *
            from strategy_templates
            {where_sql}
            order by "order" asc nulls last, created_at desc
        """
        return dump_json(pg.query(sql))
    except Exception as exc:
        return f"获取交易策略模板失败: {exc}"


def get_strategy_signals(
    ticker: str | None = None,
    strategy_name: str | None = None,
    signal_quality: str | None = None,
    limit: int = 50,
) -> str:
    """查询系统策略触发的历史信号 (strategy_signals)。"""
    try:
        clauses: list[str] = []
        params: list = []
        if ticker:
            clauses.append("upper(ticker) = %s")
            params.append(ticker.strip().upper())
        if strategy_name:
            clauses.append("strategy_name = %s")
            params.append(strategy_name.strip())
        if signal_quality:
            clauses.append("signal_quality = %s")
            params.append(signal_quality.strip())
        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        sql = f"""
            select *
            from strategy_signals
            {where_sql}
            order by ts_utc desc
            limit %s
        """
        return dump_json(pg.query(sql, tuple(params) + (int(limit),)))
    except Exception as exc:
        return f"查询策略信号失败: {exc}"


def get_option_ohlcv(
    option_code: str,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 500,
) -> str:
    """获取指定期权合约在时间段内的 1 分钟历史 K 线 (option_ohlcv_1min)。"""
    try:
        clauses = ["option_code = %s"]
        params: list = [option_code.strip()]
        if start_time:
            clauses.append("ts >= %s")
            params.append(start_time.strip())
        if end_time:
            clauses.append("ts <= %s")
            params.append(end_time.strip())
        sql = f"""
            select *
            from option_ohlcv_1min
            where {' and '.join(clauses)}
            order by ts asc
            limit %s
        """
        return dump_json(pg.query(sql, tuple(params) + (int(limit),)))
    except Exception as exc:
        return f"获取期权历史K线失败: {exc}"


def get_option_market_snapshots(
    trade_id: str | None = None,
    option_code: str | None = None,
    limit: int = 100,
) -> str:
    """获取指定交易或期权合约的实时行情与 Greeks 快照 (option_market_snapshots)。"""
    if not trade_id and not option_code:
        return "错误: trade_id 和 option_code 必须至少提供一个进行查询。"
    try:
        clauses: list[str] = []
        params: list = []
        if trade_id:
            clauses.append("trade_id = %s::uuid")
            params.append(trade_id.strip())
        if option_code:
            clauses.append("option_code = %s")
            params.append(option_code.strip())
        sql = f"""
            select *
            from option_market_snapshots
            where {' and '.join(clauses)}
            order by ts asc
            limit %s
        """
        return dump_json(pg.query(sql, tuple(params) + (int(limit),)))
    except Exception as exc:
        return f"获取期权行情快照与Greeks失败: {exc}"
