# -*- coding: utf-8 -*-
"""迁移工具共用的序列化、方向映射与市场推断辅助函数。"""
from datetime import date, datetime, time
import json
import re
import uuid

import config
from tools.hourly import _infer_market

_OCC_OPTION_RE = re.compile(r"^([A-Z]+)\s*(\d{6})([CP])(\d{8})$")


def parse_occ_code(code: str | None) -> tuple[str, str, str, str] | None:
    """解析标准 OCC 期权代码，返回 (ticker, yymmdd, right, strike_str)。"""
    if not code:
        return None
    m = _OCC_OPTION_RE.match(code.strip().upper())
    if not m:
        return None
    strike = f"{int(m.group(4)) / 1000.0:g}"
    return m.group(1), m.group(2), m.group(3), strike


def short_option_contract(code: str | None, ticker: str | None = None) -> str | None:
    """将 OCC 期权代码或历史组合代码压缩为易读短串（如 '261106 738P'）。"""
    if not code:
        return None
    s = code.strip()
    parsed = parse_occ_code(s)
    if parsed:
        _, exp, right, strike = parsed
        return f"{exp} {strike}{right}"
    if ticker and s.upper().startswith(ticker.strip().upper() + " "):
        return s[len(ticker.strip()) + 1:].strip()
    return s


def short_spread_contract(
    buy_code: str | None,
    sell_code: str | None,
    ticker: str | None = None,
) -> str | None:
    """将双腿合约压缩为紧凑价差简称（如 '261117 652P/650P' 或 '261218/261113 15C'）。"""
    b = parse_occ_code(buy_code)
    s = parse_occ_code(sell_code)
    if b and s:
        _, b_exp, b_right, b_strike = b
        _, s_exp, s_right, s_strike = s
        if b_exp == s_exp:
            return f"{b_exp} {b_strike}{b_right}/{s_strike}{s_right}"
        if b_strike == s_strike and b_right == s_right:
            return f"{b_exp}/{s_exp} {b_strike}{b_right}"
        return f"{b_exp} {b_strike}{b_right}/{s_exp} {s_strike}{s_right}"
    parts = [p for p in (
        short_option_contract(buy_code, ticker),
        short_option_contract(sell_code, ticker),
    ) if p]
    return "/".join(parts) if parts else None


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
