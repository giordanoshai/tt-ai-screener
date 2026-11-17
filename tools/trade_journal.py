# -*- coding: utf-8 -*-
"""交易复盘与期权流水工具（由 TTAiTradingSystem 迁入）。

包含 `trades` / `executions` / `strategy_templates` / `strategy_signals` /
`option_ohlcv_1min` / `option_market_snapshots` 的只读查询，统一走 `db.pg`。
"""
from datetime import datetime
import re

from db import pg
from tools._common import calc_mfe_capture_rate, dump_json, normalize_direction


def _clean_note(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", "", html)
    return re.sub(r"\s+", " ", text).strip()[:200]


def _format_minute(val) -> str:
    if not val:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M")
    s = str(val).replace("T", " ").split("+", 1)[0]
    return s[:16] if len(s) >= 16 else s


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
        clauses.append("upper(direction) = %s")
        params.append(norm_dir)
    if start_date:
        clauses.append("entry_time >= %s")
        params.append(start_date.strip())
    if end_date:
        clauses.append("entry_time <= %s")
        params.append(f"{end_date.strip()}T23:59:59Z")

    where_sql = f"where {' and '.join(clauses)}" if clauses else ""
    return where_sql, params


def get_recent_trades(
    limit: int = 10,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    trade_type: str | None = None,
    ticker: str | None = None,
    direction: str | None = None,
) -> str:
    """获取最近的交易记录 (trades)。

    可选参数:
    - limit: 返回的最大数量，默认 10
    - status: 'OPEN' / 'CLOSED' / 'PARTIAL'（大小写不敏感）
    - start_date, end_date: ISO 日期，如 '2026-05-01'
    - trade_type: 'PAPER' 或 'STRAND'
    - ticker: 标的代码，如 'AAPL'
    - direction: 'LONG'/'SHORT'（支持 'L'/'S'/'做多'/'做空' 别名）
    """
    try:
        where_sql, params = _build_trade_where(
            status, start_date, end_date, trade_type, ticker, direction
        )
        sql = f"""
            select *
            from trades
            {where_sql}
            order by entry_time desc nulls last
            limit %s
        """
        trades = pg.query(sql, tuple(params) + (int(limit),))

        seen_dates: set[str] = set()
        for trade in trades:
            eff = trade.get("entry_time") or trade.get("created_at")
            if not eff:
                continue
            date_str = str(eff)[:10]
            if date_str in seen_dates:
                if "daily_market_snapshot" in trade:
                    trade["daily_market_snapshot"] = (
                        "[Omitted to save tokens. See other trades on this date]"
                    )
            else:
                seen_dates.add(date_str)
        return dump_json(trades)
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
    """以轻量化 Schema+Array 结构获取交易记录（含偏差标签、MFE 捕获率与大盘体制）。"""
    try:
        where_sql, params = _build_trade_where(
            status, start_date, end_date, trade_type, ticker, direction
        )
        sql = f"""
            select
                id, display_id, ticker, direction, entry_price, exit_price,
                total_qty, total_pnl, total_pnl_percent, r_multiple,
                entry_time, hold_duration_minutes, metrics,
                daily_market_snapshot, would_take_again, review_note
            from trades
            {where_sql}
            order by entry_time desc nulls last
            limit %s
        """
        trades = pg.query(sql, tuple(params) + (int(limit),))

        trade_ids = [str(t["id"]) for t in trades if t.get("id")]
        flags_by_trade: dict[str, list[str]] = {}
        if trade_ids:
            ex_rows = pg.query(
                """
                select trade_id::text as trade_id, behavior_snapshot
                from executions
                where trade_id = any(%s::uuid[])
                """,
                (trade_ids,),
            )
            for ex in ex_rows:
                tid = ex.get("trade_id")
                flags = (ex.get("behavior_snapshot") or {}).get("deviation_flags") or []
                if not tid or not flags:
                    continue
                bucket = flags_by_trade.setdefault(tid, [])
                for f in flags:
                    if f not in bucket:
                        bucket.append(f)

        schema = [
            "display_id", "ticker", "dir", "entry", "exit", "qty", "pnl", "r",
            "entry_time", "hold_min", "mae_pct", "mfe_pct", "mfe_capture",
            "deviation_flags", "regime", "would_again", "note",
        ]
        rows = []
        for t in trades:
            d = str(t.get("direction") or "")
            dir_val = "L" if d == "LONG" else ("S" if d == "SHORT" else d[:1])
            metrics = t.get("metrics") or {}
            mfe_pct = metrics.get("mfe_percent")
            capture = calc_mfe_capture_rate(
                t.get("total_pnl_percent"), mfe_pct, t.get("total_pnl")
            )
            snapshot = t.get("daily_market_snapshot") or {}
            rows.append([
                t.get("display_id"),
                t.get("ticker"),
                dir_val,
                t.get("entry_price"),
                t.get("exit_price"),
                t.get("total_qty"),
                t.get("total_pnl"),
                t.get("r_multiple"),
                _format_minute(t.get("entry_time")),
                t.get("hold_duration_minutes"),
                metrics.get("mae_percent"),
                mfe_pct,
                None if capture is None else round(capture, 3),
                flags_by_trade.get(str(t.get("id")), []),
                snapshot.get("regime"),
                t.get("would_take_again"),
                _clean_note(t.get("review_note")),
            ])

        return dump_json({"schema": schema, "rows": rows})
    except Exception as exc:
        return f"获取轻量化交易记录失败: {exc}"


def get_trade_details(trade_id: str) -> str:
    """获取指定交易记录的详细信息（含 executions 分笔成交、偏差标签与 MFE 捕获率）。

    trade_id 支持传入 display_id（数字）或 UUID。
    """
    try:
        tid_str = str(trade_id).strip()
        if len(tid_str) > 10 and "-" in tid_str:
            trade = pg.query_one("select * from trades where id = %s::uuid", (tid_str,))
        else:
            trade = pg.query_one("select * from trades where display_id = %s", (int(tid_str),))

        if not trade:
            return f"未找到交易: {trade_id}"

        executions = pg.query(
            """
            select id, trade_id, broker_order_id, side, action_type, qty, price,
                   commission, transaction_time, created_at, market_snapshot,
                   strategy, strategy_params, psychology, behavior_snapshot,
                   target_transaction_time
            from executions
            where trade_id = %s::uuid
            order by transaction_time asc
            """,
            (str(trade["id"]),),
        )

        deviation_flags: list[str] = []
        for ex in executions:
            for f in (ex.get("behavior_snapshot") or {}).get("deviation_flags") or []:
                if f not in deviation_flags:
                    deviation_flags.append(f)

        metrics = trade.get("metrics") or {}
        capture = calc_mfe_capture_rate(
            trade.get("total_pnl_percent"),
            metrics.get("mfe_percent"),
            trade.get("total_pnl"),
        )
        trade["deviation_flags"] = deviation_flags
        trade["mfe_capture"] = None if capture is None else round(capture, 3)
        trade["executions"] = executions
        return dump_json(trade)
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
