# -*- coding: utf-8 -*-
"""期权和资金面的取数层：期权贵不贵、量比、有没有人在建新仓。

## 这个模块补的是什么缺口

2026-09-03 对着 `tt-data-hub` 写的表和本仓库读的表做了一次差集，发现四张
**每天都在写、这边一行代码都没读过**的表。全是期权和资金面的数据，
而本仓库原来在这一块只有两样东西：逐笔期权异动的溢价净额
（`flow_alerts_archive`）和伽马墙（`uw_gex_daily`）。

| 表 | 每天多少 | 补上了什么 |
|---|---|---|
| `uw_screener_daily` | 500 只 | **期权现在贵不贵**（IV 排名）、财报预期波动幅度 |
| `uw_options_volume_daily` | 410 只 | 整只票的期权量和未平仓量，能跟 30 日均量比 |
| `uw_oi_change_daily` | 410 只 | 逐个合约的未平仓量变化 |
| `uw_market_ibs_daily` | 1 行 | 全市场内部人买卖的汇总 |

**为什么未平仓量这么重要**：成交量大只说明「有人在交易」，可能是新建仓，
也可能是原来的人在平仓了结。未平仓合约数（OI）涨了才说明**真有新的钱押进来**。
原来那份逐笔异动数据分不出这两种，看到「今天 NVDA 期权成交 5000 万」时，
到底是有人押新的还是有人跑路，纯靠猜。

## 三件必须知道的事

1. **只有美股。** Unusual Whales 不做 A股，这四张表里没有一行 CN。
   A股调用一律返回空，调用方照常走它自己那套（主力资金、龙虎榜）。
2. **覆盖面不是全市场。** 500 只 / 410 只，而美股有两千多只。取不到是常态，
   不是故障——所有函数都返回 `{}` 或 `None`，不抛。
3. **量级混着放，逐个字段确认**（这个坑在本仓库炸过三次，见 CLAUDE.md
   「比率字段的单位约定」）：

       iv_rank        0~100 的排名      7.2952  → 直接用
       iv30d          小数              0.327   → 乘 100 变成 32.7%
       implied_move   **美元金额**      14.301  → NVDA 股价 224，14.3 美元
                                                  ≈ 6.4%，跟它财报的实际波动
                                                  对得上；当成百分比会差 2 倍
       gex_ratio      比值              0.534   → 直接用
       oi_change      **倍数**          2.9857  → 未平仓涨到原来的 2.99 倍，
                                                  不是涨了 2.99%
"""
import datetime
import json

from db import pg

#: 期权合约代码（OCC 格式）的尾巴：6 位到期日 + 1 位 C/P + 8 位行权价。
#: NVDA270115P00100000 → NVDA / 2027-01-15 / put / 100.0
_OCC_TAIL = 15
_OCC_STRIKE_DIVISOR = 1000.0

#: 未平仓量变化一次给 50 个合约，交出去只留增加最多的几个。
#: 50 个原样丢给 Claude 是 6000 多字符，而看的人只关心「钱押在哪几张合约上」。
TOP_OI_BUILDS = 3

#: 全市场内部人买卖看最近多少天。5 天≈一周，够看出「这周高管在集体减持」。
INSIDER_TIDE_DAYS = 5

#: 这几张表只有美股。
MARKET = "US"


# 每张表都是 (snapshot_date, ticker) + 一个 jsonb payload，
# 取最新那天用 distinct on，不用相关子查询（那个在大表上是全表扫，
# 见 CLAUDE.md 的第三条规矩；这几张表虽然小，写法也保持一致）。
_SCREENER_SQL = """
select distinct on (ticker) ticker, snapshot_date, payload
from uw_screener_daily
where ticker = any(%s)
order by ticker, snapshot_date desc
"""

_VOLUME_SQL = """
select distinct on (ticker) ticker, snapshot_date, payload
from uw_options_volume_daily
where ticker = any(%s)
order by ticker, snapshot_date desc
"""

_OI_SQL = """
select ticker, snapshot_date, payload
from uw_oi_change_daily
where ticker = %s
order by snapshot_date desc
limit 1
"""

_IBS_SQL = """
select snapshot_date, payload
from uw_market_ibs_daily
order by snapshot_date desc
limit 1
"""


def _payload(value):
    """payload 列是 jsonb，psycopg2 一般直接给 dict/list；字符串就再解一次。"""
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


def _num(value, digits=2, scale=1.0):
    """UW 把数值全用字符串返回。转不动就返回 None，不要拿 0 冒充。"""
    if value is None:
        return None
    try:
        return round(float(value) * scale, digits)
    except (TypeError, ValueError):
        return None


def _int(value):
    """成交量、未平仓量、整数美元这类。转不动返回 None。

    专门转成 int 是为了省字符：JSON 里 `3603522` 比 `3603522.0` 短两位，
    而这几个字段一次返回有八九个。小数位对「今天成交了三百六十万张合约」
    这种量级也没有任何意义。
    """
    num = _num(value, 0)
    return None if num is None else int(num)


def _clean(tickers) -> list[str]:
    return [t.upper().strip() for t in (tickers or []) if t and t.strip()]


def parse_occ(symbol: str) -> dict | None:
    """把期权合约代码拆开。`NVDA270115P00100000` → 到期日 / 方向 / 行权价。

    拆不动返回 None——代码格式变了不该让整只票的分析挂掉。
    """
    symbol = (symbol or "").strip().upper()
    if len(symbol) <= _OCC_TAIL:
        return None
    tail = symbol[-_OCC_TAIL:]
    try:
        expiry = datetime.datetime.strptime(tail[:6], "%y%m%d").date()
        strike = int(tail[7:]) / _OCC_STRIKE_DIVISOR
    except ValueError:
        return None
    if tail[6] not in ("C", "P"):
        return None
    return {
        "expiry": expiry.isoformat(),
        "type": "call" if tail[6] == "C" else "put",
        "strike": round(strike, 2),
    }


def iv_snapshot(tickers: list[str]) -> dict[str, dict]:
    """期权现在贵不贵，以及财报预期能动多少。

    白话：`iv_rank` 是这只票的期权价格在过去一年里排第几（0~100）。
    80 以上说明期权很贵——想买入期权做多的人这时候进场是在付高价，
    反过来卖期权收权利金的人这时候最划算；20 以下反过来。

    取不到的票不出现在返回里（覆盖面只有 500 只，缺是常态）。
    """
    tickers = _clean(tickers)
    if not tickers:
        return {}

    out = {}
    for row in pg.query(_SCREENER_SQL, (tickers,)):
        data = _payload(row["payload"])
        if not isinstance(data, dict):
            continue
        close = _num(data.get("close"))
        move = _num(data.get("implied_move"))
        out[row["ticker"]] = {
            "as_of": str(row["snapshot_date"]),
            # 0~100 的排名，原样用
            "iv_rank": _num(data.get("iv_rank"), 1),
            # 库里是小数 0.327，换成人看的 32.7%
            "iv30d_pct": _num(data.get("iv30d"), 1, scale=100),
            "iv30d_1w_ago_pct": _num(data.get("iv30d_1w"), 1, scale=100),
            "iv30d_1m_ago_pct": _num(data.get("iv30d_1m"), 1, scale=100),
            # **美元金额**，不是百分比。再换算一个百分比出来省得看的人自己算
            "implied_move": move,
            "implied_move_pct": (round(move / close * 100, 2)
                                 if move and close else None),
            "gex_ratio": _num(data.get("gex_ratio"), 2),
            "sector": data.get("sector"),
        }
    return out


def flow_volume(tickers: list[str]) -> dict[str, dict]:
    """整只票的期权成交量和未平仓量，跟自己的 30 日均量比。

    白话：`volume_vs_30d` 是 3.0 就表示今天期权成交量是平时的三倍——
    有人在这只票上突然动手了。`put_call_ratio` 大于 1 说明买保险的比看涨的多。

    这跟 `core/catalyst.py::options_flow` 不是一回事：那边看的是**大单异动的
    钱数**（谁往里砸了多少钱），这边看的是**整只票的量和持仓量**（有多少张
    合约在手上）。两个都要，缺一个都会误判。
    """
    tickers = _clean(tickers)
    if not tickers:
        return {}

    out = {}
    for row in pg.query(_VOLUME_SQL, (tickers,)):
        data = _payload(row["payload"])
        if not isinstance(data, dict):
            continue
        calls = _int(data.get("call_volume"))
        puts = _int(data.get("put_volume"))
        avg30 = ((_int(data.get("avg_30_day_call_volume")) or 0)
                 + (_int(data.get("avg_30_day_put_volume")) or 0))
        total = (calls or 0) + (puts or 0)
        out[row["ticker"]] = {
            "as_of": str(row["snapshot_date"]),
            "call_volume": calls,
            "put_volume": puts,
            "put_call_ratio": (round(puts / calls, 2)
                               if calls and puts is not None else None),
            "volume_vs_30d": round(total / avg30, 2) if avg30 else None,
            "call_open_interest": _int(data.get("call_open_interest")),
            "put_open_interest": _int(data.get("put_open_interest")),
            "net_call_premium": _int(data.get("net_call_premium")),
            "net_put_premium": _int(data.get("net_put_premium")),
        }
    return out


def oi_builds(ticker: str, top: int = TOP_OI_BUILDS) -> list[dict]:
    """未平仓量增加最多的几张合约——**有人在建新仓**。

    白话：成交量大只说明有人在交易，可能是新押注，也可能是原来的人在平仓
    了结。未平仓合约数（OI）从 46,501 张涨到 185,341 张，才说明真有新的钱
    押进来了。这是那份逐笔异动数据回答不了的问题。

    返回按「增加了多少张」从多到少排，每张合约带到期日、方向、行权价。
    取不到返回空列表。
    """
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return []

    row = pg.query_one(_OI_SQL, (ticker,))
    if not row:
        return []
    rows = _payload(row["payload"])
    if not isinstance(rows, list):
        return []

    builds = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        added = item.get("oi_diff_plain")
        if not isinstance(added, (int, float)) or added <= 0:
            continue      # 只看增加的；减少的是平仓，另一回事
        contract = parse_occ(item.get("option_symbol") or "") or {}
        builds.append({
            "as_of": str(row["snapshot_date"]),
            **contract,
            "oi_added": int(added),
            "oi_now": item.get("curr_oi"),
            # 库里是**倍数**：2.99 表示涨到原来的 2.99 倍，不是涨 2.99%
            "oi_multiple": _num(item.get("oi_change"), 2),
            "volume": item.get("volume"),
            "days_of_oi_increases": item.get("days_of_oi_increases"),
        })

    builds.sort(key=lambda b: b["oi_added"], reverse=True)
    return builds[:max(int(top), 1)]


def insider_tide(days: int = INSIDER_TIDE_DAYS) -> dict | None:
    """全市场的内部人在买还是在卖，最近几天合起来看。

    白话：公司高管和董事自己掏钱买自家股票，是最实在的看多信号；
    集体减持则相反。单看一只票噪音很大（高管卖股票可能只是要交税），
    但**全市场几百笔合起来**就能看出风向。

    实测 2026-09-02 那天：卖 127 笔共 2.63 亿美元，买 21 笔共 360 万——
    卖出金额是买入的 73 倍。

    取不到返回 None。
    """
    row = pg.query_one(_IBS_SQL)
    if not row:
        return None
    rows = _payload(row["payload"])
    if not isinstance(rows, list) or not rows:
        return None

    window = [r for r in rows[:max(int(days), 1)] if isinstance(r, dict)]
    if not window:
        return None

    buys = sum(int(r.get("purchases") or 0) for r in window)
    sells = sum(int(r.get("sells") or 0) for r in window)
    # sells_notional 库里是负数，取绝对值再比
    buy_amount = sum(abs(_num(r.get("purchases_notional"), 2) or 0) for r in window)
    sell_amount = sum(abs(_num(r.get("sells_notional"), 2) or 0) for r in window)

    return {
        "days": len(window),
        "from": window[-1].get("filing_date"),
        "to": window[0].get("filing_date"),
        "buy_count": buys,
        "sell_count": sells,
        "buy_amount": round(buy_amount, 2),
        "sell_amount": round(sell_amount, 2),
        # 卖出金额是买入的几倍。历史上常年大于 1（高管本来就更多在卖），
        # 所以要看的是它跟平时比高了还是低了，不是「大于 1 就是利空」
        "sell_over_buy": (round(sell_amount / buy_amount, 1)
                          if buy_amount else None),
    }
