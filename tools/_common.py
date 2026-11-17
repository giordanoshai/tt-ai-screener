# -*- coding: utf-8 -*-
"""迁移工具共用的序列化、方向映射与市场推断辅助函数。"""
from datetime import date, datetime, time
import json
import uuid

import config
from tools.hourly import _infer_market


def _json_default(obj):
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    return str(obj)


def dump_json(data) -> str:
    """紧凑 JSON 输出（无缩进，保留中文，兼容 Postgres 日期与 UUID）。"""
    return json.dumps(data, ensure_ascii=False, default=_json_default)


def normalize_direction(direction: str | None) -> str | None:
    """将做多/做空别名规范为 LONG / SHORT。"""
    if not direction:
        return None
    upper = direction.strip().upper()
    if upper in ("LONG", "L", "做多", "BUY"):
        return "LONG"
    if upper in ("SHORT", "S", "做空", "SELL"):
        return "SHORT"
    return upper


def calc_mfe_capture_rate(
    total_pnl_percent: float | None,
    mfe_percent: float | None,
    total_pnl: float | None,
) -> float | None:
    """最高浮盈捕获率：盈利单吃到了最高浮盈的多少（0~1+）。"""
    if total_pnl is None or total_pnl <= 0:
        return None
    if total_pnl_percent is None or mfe_percent is None or mfe_percent == 0:
        return None
    return (total_pnl_percent * 100.0) / mfe_percent


def resolve_market(ticker: str | None, market: str | None = None) -> str:
    """解析目标市场（US / CN），未指定时按代码后缀推断，默认 US。"""
    if market:
        m = market.strip().upper()
        if m in ("US", "CN"):
            return m
        raise ValueError(f"Unsupported market: {market!r} (expected 'US' or 'CN')")
    if ticker:
        inferred = _infer_market(ticker.strip().upper())
        if inferred:
            return inferred
    return "US"


def default_user_id() -> str:
    return config.WATCHLIST_USER_ID
