# -*- coding: utf-8 -*-
"""A股独立信号：主力资金、龙虎榜、板块排名、涨停情绪。

## A股不是「美股那套的残缺版」

A股个股没有期权，所以美股那个「期权异动确认突破真假」的信号在这里不存在。
但 A股有美股没有的东西：主力资金明细（谁在买）、龙虎榜（游资进出）、
强烈的板块轮动、涨停家数和炸板率（市场情绪温度）。

对应关系：**主力资金净流入在 A股扮演美股的期权异动**——都是回答
「这个突破背后有没有钱」。

## 金额单位统一是元

采集端已经把两种口径拉平了：个股资金流原本是「1.04亿」这种字符串，
板块资金流原本是以亿元为单位的裸数字，现在库里两张表存的都是**元**。
下游不要再乘除一次（那会造成 1 亿倍的误差，而且不报错）。

## 已知缺口：没有个股 → 行业的映射

`cn_universe.industry` 和 `stocks_meta.industry` 对 A股全是 NULL（实测）。
唯一带行业名的是 `cn_limit_pool_daily.industry`，只覆盖当日涨停的票，
且只有 48/82 能对上板块名。

所以 `board_of()` 只对涨停过的票能给出板块，其余返回 None，调用方要把
`BOARD_MAPPING_GAP` 写进 data_gaps。不静默降级——一个看起来算过板块、
实际没算的评分，比明说「这里缺数据」危险得多。

补上这个映射要在 tt-data-hub 做（把行业成分表灌进 cn_universe.industry），
不在本仓库。
"""
from db import pg

BOARD_TYPES = ("industry", "concept")

#: 缺个股→行业映射时写进 data_gaps 的那句话。见模块注释。
BOARD_MAPPING_GAP = ("A股缺个股所属行业的映射（cn_universe.industry 是空的），"
                     "板块强度只能给全市场排名，给不了这只票所在的板块")

_FUND_FLOW_SQL = """
select ticker, name, snapshot_date, close, pct_chg, turnover_pct,
       inflow, outflow, net_amount, amount,
       net_5d, pct_chg_5d, turnover_5d
from cn_fund_flow_daily
where ticker = any(%s)
  and snapshot_date = (select max(snapshot_date) from cn_fund_flow_daily)
"""

_LHB_SQL = """
select ticker, name, list_date, reason, interpretation,
       close, pct_chg, net_buy, buy_amount, sell_amount,
       net_buy_pct, turnover_pct, ret_1d, ret_5d
from cn_lhb_daily
where ticker = any(%s)
  and list_date >= current_date - %s
order by list_date desc
"""

_BOARD_SQL = """
select board_name, board_index, pct_chg, net_amount, inflow, outflow,
       company_count, leader_name, leader_pct_chg, snapshot_date
from cn_board_flow_daily
where board_type = %s
  and snapshot_date = (select max(snapshot_date) from cn_board_flow_daily
                       where board_type = %s)
order by net_amount desc
"""

# 只有涨停板那张表带行业名。这是目前唯一的个股→行业线索，覆盖面很小。
_BOARD_OF_SQL = """
select l.ticker, l.industry, b.net_amount, b.pct_chg, b.company_count
from cn_limit_pool_daily l
left join cn_board_flow_daily b
       on b.board_name = l.industry
      and b.board_type = 'industry'
      and b.snapshot_date = l.snapshot_date
where l.ticker = any(%s)
  and l.snapshot_date = (select max(snapshot_date) from cn_limit_pool_daily)
"""

_LIMIT_SQL = """
select max(snapshot_date) as date,
       count(*) as limit_up_count,
       count(*) filter (where open_times > 0) as broken_count,
       max(limit_streak) as max_streak
from cn_limit_pool_daily
where snapshot_date = (select max(snapshot_date) from cn_limit_pool_daily)
"""

_DATES_SQL = """
select (select max(snapshot_date) from cn_fund_flow_daily)  as fund_flow,
       (select max(snapshot_date) from cn_board_flow_daily) as board_flow,
       (select max(snapshot_date) from cn_limit_pool_daily) as limit_pool,
       (select max(list_date)     from cn_lhb_daily)        as lhb
"""


def _clean(tickers) -> list[str]:
    return [t.upper().strip() for t in (tickers or []) if t and t.strip()]


def _f(value):
    return None if value is None else float(value)


def fund_flow(tickers: list[str]) -> dict[str, dict | None]:
    """主力资金：当日净流入 + 5 日净流入。金额单位是元。"""
    tickers = _clean(tickers)
    if not tickers:
        return {}
    found = {}
    for row in pg.query(_FUND_FLOW_SQL, (tickers,)):
        found[row["ticker"]] = {
            "snapshot_date": row["snapshot_date"],
            "name": row.get("name"),
            "net_amount": _f(row.get("net_amount")),
            "net_5d": _f(row.get("net_5d")),
            "inflow": _f(row.get("inflow")),
            "outflow": _f(row.get("outflow")),
            "amount": _f(row.get("amount")),
            "pct_chg": _f(row.get("pct_chg")),
            "pct_chg_5d": _f(row.get("pct_chg_5d")),
            "turnover_pct": _f(row.get("turnover_pct")),
        }
    return {t: found.get(t) for t in tickers}


def lhb(tickers: list[str], days: int = 30) -> dict[str, list[dict]]:
    """龙虎榜记录。同一天同一只票可能有多条（多个上榜原因）。"""
    tickers = _clean(tickers)
    if not tickers:
        return {}
    out: dict[str, list[dict]] = {t: [] for t in tickers}
    for row in pg.query(_LHB_SQL, (tickers, days)):
        out.setdefault(row["ticker"], []).append({
            "list_date": row["list_date"],
            "reason": row.get("reason"),
            "interpretation": row.get("interpretation"),
            "net_buy": _f(row.get("net_buy")),
            "net_buy_pct": _f(row.get("net_buy_pct")),
            "pct_chg": _f(row.get("pct_chg")),
            "ret_1d": _f(row.get("ret_1d")),
            "ret_5d": _f(row.get("ret_5d")),
        })
    return out


def board_ranking(board_type: str = "industry", top: int = 10) -> dict:
    """板块资金排名。A股是板块市，个股再好逆着板块也难做。"""
    board_type = (board_type or "").lower()
    if board_type not in BOARD_TYPES:
        raise ValueError(f"board_type 只能是 industry 或 concept，"
                         f"收到 {board_type!r}")
    rows = pg.query(_BOARD_SQL, (board_type, board_type))
    shaped = [{
        "board_name": r["board_name"],
        "net_amount": _f(r.get("net_amount")),
        "pct_chg": _f(r.get("pct_chg")),
        "company_count": r.get("company_count"),
        "leader_name": r.get("leader_name"),
        "leader_pct_chg": _f(r.get("leader_pct_chg")),
        "rank": i + 1,
    } for i, r in enumerate(rows)]
    return {
        "board_type": board_type,
        "date": rows[0]["snapshot_date"] if rows else None,
        "total_boards": len(shaped),
        "top": shaped[:top],
        "bottom": shaped[-top:][::-1],
        # 全量排名表。board_of() 要用它把行业名换成名次，
        # 只给首尾各 10 个的话大多数板块查不到名次。
        "all_ranks": {b["board_name"]: b["rank"] for b in shaped},
    }


def board_of(tickers: list[str]) -> dict[str, dict | None]:
    """这几只票各属于哪个行业板块、那个板块今天强不强。

    **大多数票会是 None**，因为库里没有个股→行业的映射，只有当日涨停的票
    带行业名。见模块注释和 BOARD_MAPPING_GAP。
    """
    tickers = _clean(tickers)
    if not tickers:
        return {}

    ranking = board_ranking("industry")
    rank_by_name = ranking["all_ranks"]

    found = {}
    for row in pg.query(_BOARD_OF_SQL, (tickers,)):
        name = row.get("industry")
        if not name:
            continue
        found[row["ticker"]] = {
            "board_name": name,
            "net_amount": _f(row.get("net_amount")),
            "pct_chg": _f(row.get("pct_chg")),
            "company_count": row.get("company_count"),
            "rank": rank_by_name.get(name),
            "total_boards": ranking["total_boards"],
        }
    return {t: found.get(t) for t in tickers}


def limit_sentiment() -> dict:
    """涨停家数、炸板率、最高连板。A股的市场情绪温度计。"""
    row = pg.query_one(_LIMIT_SQL) or {}
    limit_up = row.get("limit_up_count") or 0
    broken = row.get("broken_count") or 0
    return {
        "date": row.get("date"),
        "limit_up_count": limit_up,
        "broken_count": broken,
        "broken_rate": round(broken / limit_up, 3) if limit_up else None,
        "max_streak": row.get("max_streak"),
    }


def signal_dates() -> dict:
    """四张 A股信号表各自最新到哪天，以及它们齐不齐。

    这四张表是四个独立的采集任务写的，进度可能不一致。把三个不同交易日的
    资金流、板块和情绪拼成一个结论而不说破，用户会以为看到的是同一天的
    东西——那正是设计文档第八节禁止的静默降级。

    龙虎榜天然比别的晚（当天收盘后才披露），所以它不参与齐不齐的判断，
    只把日期报出来。
    """
    row = pg.query_one(_DATES_SQL) or {}
    same_day = {row.get(k) for k in ("fund_flow", "board_flow", "limit_pool")}
    in_sync = len(same_day - {None}) <= 1
    return {
        "fund_flow": row.get("fund_flow"),
        "board_flow": row.get("board_flow"),
        "limit_pool": row.get("limit_pool"),
        "lhb": row.get("lhb"),
        "in_sync": in_sync,
        "gap": None if in_sync else (
            f"A股四张信号表不在同一天："
            f"资金流 {row.get('fund_flow')}、板块 {row.get('board_flow')}、"
            f"涨停 {row.get('limit_pool')}。跨天的数据不能当成同一天读"),
    }
