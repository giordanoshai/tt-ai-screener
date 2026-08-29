# -*- coding: utf-8 -*-
"""分析引擎的取数层：把库里的数据拼成一份上下文，交给 AI 模型去分析。

`analyze_watchlist` 和 `analyze_positions` 两个 MCP 工具都靠这里取数，
网页端的「分析」按钮也走这里。它们都是**在函数体里** import 本模块的，
所以这里坏掉的时候 import 阶段不报错，要等真调用才炸，然后被外面的
except 吞成 `{"error": ...}`——从工具返回值上看不出是这一层坏了。
所以改这个文件的时候，务必跑 `tests/test_skills.py`。

数据源已经从本地 DuckDB 换成自建 Postgres（采集那一摊在 tt-data-hub），
移植时踩过的坑，每一条都能让查询直接报错或者悄悄给出错误结果：

1. **占位符是 `%s`，不是 DuckDB 的 `?`。** 写成 `?` 会报
   `syntax error at or near "?"`。
2. **每条查日线表的语句都要带 `market` 条件。** `stock_ohlcv_daily` 混了
   美股（275 万行）和 A股（105 万行），漏了会把 A股串进美股结果里。
3. **不要用相关子查询取最新交易日**
   （`o.date = (select max(date) from stock_ohlcv_daily)`），在 Postgres 上是
   全表扫。两种正确写法，按票数多少选：
   - 票少（自选、持仓，几十只）→ 按票走主键索引倒扫 `order by date desc limit 1`；
   - 扫全池（内置的两个筛选）→ 先单独查出这个市场的最新交易日，再等值比较。
4. **`stock_fundamentals` 一只票有很多行**（主键是 ticker + updated_at，
   2135 只票摊了 29943 行）。直接连表一只票会出来十几行，必须用
   `distinct on (ticker)` 取最新那条。
5. **`news` 表的列名跟原来完全不一样**：没有 `ticker` 列，是
   `related_tickers`（text 数组，有 GIN 索引，所以要用 `&&` 数组重叠去查）；
   没有 `headline` 列，标题在 `title` 里。对外输出仍然叫 `headline`——
   技能提示词（skills/*/*.md）里约定的就是这个名字，改名会让提示词对不上。
6. **A股没有基本面数据。** `stock_fundamentals` 现在只有美股，所以 A股的票
   查出来基本面字段全是空的，这是正常的，不是 bug。
"""
import json
import os
from datetime import date
from pathlib import Path

from db import pg

SKILLS_DIR = Path(__file__).parent

#: 自定义提示词存这里。测试会把这个名字指到临时文件上。
PROMPTS_JSON = SKILLS_DIR.parent / "config" / "skill_prompts.json"

SKILL_NAMES = ["longterm_screen", "swing_screen", "position_review"]
SUPPORTED_LANGS = ["en", "cn"]
DEFAULT_LANG = "en"

# A股代码后缀：上交所 / 深交所 / 北交所。跟 tools/hourly.py 用的是同一套规则。
_CN_SUFFIXES = (".SH", ".SZ", ".BJ")
DEFAULT_MARKET = "US"


def _market_of(ticker: str) -> str:
    """从代码后缀判断市场。600519.SH 是 A股，AAPL 是美股。"""
    return "CN" if (ticker or "").upper().endswith(_CN_SUFFIXES) else DEFAULT_MARKET


def _group_by_market(tickers: list[str]) -> dict[str, list[str]]:
    """把一批代码按市场分组，好让每条查询都能带上确定的 market 条件。"""
    groups: dict[str, list[str]] = {}
    for ticker in tickers:
        groups.setdefault(_market_of(ticker), []).append(ticker)
    return groups


def get_lang() -> str:
    """输出语言。环境变量 LANG_UI（en / cn）说了算，没配就是英文。

    原来这里先查 app_settings 表拿「用户在设置页选的语言」。那张表是本地
    DuckDB 时代的东西，自建 Postgres 里没有，所以每次调用都白撞一次连接。
    2026-08-29 用户决定语言切换这个功能整个不要了，只剩这一条路。
    """
    env_lang = os.getenv("LANG_UI", "").strip().lower()
    return env_lang if env_lang in SUPPORTED_LANGS else DEFAULT_LANG


# ---------------------------------------------------------------- 自定义提示词
#
# 原来存在 skill_prompts 表里，跟 app_settings 一样迁库时漏掉了，线上没有，
# 所以设置页上「保存提示词」一直是存不进去的。现在存本地 JSON，结构是
# {"技能名": {"语言": "提示词"}}。文件不存在、坏了、或者那一条是空白，
# 都退回 skills/<lang>/<skill>.md，不报错。


def _check(skill_name: str, lang: str) -> None:
    if skill_name not in SKILL_NAMES:
        raise ValueError(f"Unknown skill: {skill_name}")
    if lang not in SUPPORTED_LANGS:
        raise ValueError(f"Unknown lang: {lang}")


def load_custom_prompts() -> dict:
    """读全部自定义提示词。文件没有或读不动都返回空字典。"""
    try:
        data = json.loads(PROMPTS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get_custom_prompt(skill_name: str, lang: str) -> str | None:
    """某个技能某种语言的自定义提示词。没有、或者存的是空白，返回 None。"""
    entry = load_custom_prompts().get(skill_name)
    if not isinstance(entry, dict):
        return None
    text = entry.get(lang)
    return text if isinstance(text, str) and text.strip() else None


def save_custom_prompt(skill_name: str, lang: str, prompt: str) -> None:
    """存一条自定义提示词，其余条目原样保留。"""
    _check(skill_name, lang)
    data = load_custom_prompts()
    data.setdefault(skill_name, {})
    if not isinstance(data[skill_name], dict):
        data[skill_name] = {}
    data[skill_name][lang] = prompt
    _write_custom_prompts(data)


def delete_custom_prompt(skill_name: str, lang: str) -> bool:
    """删掉一条自定义提示词，回到 md 文件。本来就没有返回 False。"""
    _check(skill_name, lang)
    data = load_custom_prompts()
    entry = data.get(skill_name)
    if not isinstance(entry, dict) or lang not in entry:
        return False
    entry.pop(lang)
    if not entry:
        data.pop(skill_name)
    _write_custom_prompts(data)
    return True


def _write_custom_prompts(data: dict) -> None:
    PROMPTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROMPTS_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_skill_prompt(skill_name: str, lang: str = None) -> str:
    """实际用的提示词：有自定义就用自定义，否则用 skills/ 下的 md 文件。"""
    if skill_name not in SKILL_NAMES:
        raise ValueError(f"Unknown skill: {skill_name}")
    lang = lang or get_lang()

    custom = get_custom_prompt(skill_name, lang)
    if custom:
        return custom

    return load_default_skill_prompt(skill_name, lang)


def load_default_skill_prompt(skill_name: str, lang: str = None) -> str:
    """只从 md 文件读，忽略自定义覆盖。"""
    if skill_name not in SKILL_NAMES:
        raise ValueError(f"Unknown skill: {skill_name}")
    lang = lang or get_lang()
    path = SKILLS_DIR / lang / f"{skill_name}.md"
    if not path.exists():
        path = SKILLS_DIR / DEFAULT_LANG / f"{skill_name}.md"
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ SQL


# 某个市场最新有日线的那一天。单独查一次，比在主查询里现算便宜两个数量级
# （这一句吃 (market, date desc) 索引）。
_LATEST_DAY_SQL = """
    select max(date) as d from stock_ohlcv_daily where market = %(market)s
"""

# 基本面取最新一条：主键是 (ticker, updated_at)，不去重一只票会出来十几行。
_FUND_COLUMNS = """
    revenue_growth_yoy, earnings_growth_yoy, gross_margin,
    pe_ratio, ps_ratio, pb_ratio, peg_ratio,
    analyst_rating, analyst_target_price, analyst_count,
    mspr, next_earnings_date, last_earnings_date
"""

# 按给定的一批代码取数（自选池、持仓、网页端指定的票都走这条）。
# 票数少，所以每只票单独走主键索引倒扫拿最新一根日线——停牌、退市的票
# 也能拿到它自己最后一根，不会因为「不是全市场最新那天」而整行消失。
# 驱动表是传进来的代码列表，不是行情表：自选池是用户自己维护的，
# 哪怕库里一根 K 线都没有，这只票也必须出现在结果里。
_TICKERS_SQL = f"""
    with wanted as (
        select unnest(%(tickers)s::text[]) as ticker
    ),
    fund as (
        select distinct on (ticker) ticker, {_FUND_COLUMNS}
        from stock_fundamentals
        where ticker = any(%(tickers)s::text[])
          and market = %(market)s
        order by ticker, updated_at desc
    )
    select w.ticker, m.company_name, m.sector,
           f.revenue_growth_yoy, f.earnings_growth_yoy, f.gross_margin,
           f.pe_ratio, f.ps_ratio, f.pb_ratio, f.peg_ratio,
           f.analyst_rating, f.analyst_target_price, f.analyst_count,
           f.mspr, f.next_earnings_date, f.last_earnings_date,
           d.close, d.ma_20, d.ma_50, d.ma_200, d.rsi_14,
           d.dist_ma20_pct, d.dist_ma50_pct, d.vol_ratio, d.atr_pct,
           d.high_20, d.pct_chg, d.date as as_of
    from wanted w
    left join stocks_meta m
           on m.ticker = w.ticker and m.market = %(market)s
    left join fund f on f.ticker = w.ticker
    left join lateral (
        select o.date, o.close, o.ma_20, o.ma_50, o.ma_200, o.rsi_14,
               o.dist_ma20_pct, o.dist_ma50_pct, o.vol_ratio, o.atr_pct,
               o.high_20, o.pct_chg
        from stock_ohlcv_daily o
        where o.ticker = w.ticker
          and o.market = %(market)s
        order by o.date desc
        limit 1
    ) d on true
"""

# 内置的长线筛选：增长 >10%、毛利 >30%、市盈率 <60、站上年线。
# 要扫全票池，所以用「先查出最新交易日再等值比较」那一套。
_LONGTERM_SQL = f"""
    with fund as (
        select distinct on (ticker) ticker, updated_at, {_FUND_COLUMNS}
        from stock_fundamentals
        where market = %(market)s
        order by ticker, updated_at desc
    )
    select f.ticker, m.company_name, m.sector,
           f.revenue_growth_yoy, f.earnings_growth_yoy, f.gross_margin,
           f.pe_ratio, f.ps_ratio, f.pb_ratio, f.peg_ratio,
           f.analyst_rating, f.analyst_target_price, f.analyst_count,
           f.mspr, f.next_earnings_date, f.last_earnings_date,
           o.close, o.ma_20, o.ma_50, o.ma_200, o.rsi_14,
           o.dist_ma20_pct, o.dist_ma50_pct, o.vol_ratio, o.atr_pct,
           o.high_20, o.date as as_of
    from fund f
    join stocks_meta m
      on m.ticker = f.ticker and m.market = %(market)s
    join stock_ohlcv_daily o
      on o.market = %(market)s
     and o.date = %(as_of)s
     and o.ticker = f.ticker
    where f.updated_at >= current_date - interval '30 days'
      and f.revenue_growth_yoy > 0.10
      and f.earnings_growth_yoy > 0.10
      and f.gross_margin > 0.30
      and f.pe_ratio < 60
      and o.close > o.ma_200
    order by f.revenue_growth_yoy desc
    limit 50
"""

# 内置的波段筛选：站上 20/50 日线、均线多头、放量、RSI 不极端、离 20 日线不远。
_SWING_SQL = f"""
    with fund as (
        select distinct on (ticker) ticker, {_FUND_COLUMNS}
        from stock_fundamentals
        where market = %(market)s
        order by ticker, updated_at desc
    )
    select o.ticker, m.company_name, m.sector,
           o.close, o.vol_ratio, o.rsi_14,
           o.dist_ma20_pct, o.dist_ma50_pct,
           o.high_20, o.ma_20, o.ma_50, o.ma_200,
           o.atr_pct, o.pct_chg, o.date as as_of,
           f.next_earnings_date, f.pe_ratio, f.revenue_growth_yoy
    from stock_ohlcv_daily o
    join stocks_meta m
      on m.ticker = o.ticker and m.market = o.market
    left join fund f on f.ticker = o.ticker
    where o.market = %(market)s
      and o.date = %(as_of)s
      and o.close > o.ma_20
      and o.close > o.ma_50
      and o.ma_20 > o.ma_50
      and o.vol_ratio > 1.2
      and o.rsi_14 between 45 and 75
      and o.dist_ma20_pct < 0.08
      and o.atr_pct > 0.01
    order by o.vol_ratio desc
    limit 100
"""

# 单只票的最新一根日线：走 (ticker, date) 主键索引倒扫。
_POSITION_OHLCV_SQL = """
    select ticker, date as as_of, close, ma_20, ma_50, ma_200,
           rsi_14, dist_ma20_pct, dist_ma50_pct, vol_ratio, atr_pct, pct_chg
    from stock_ohlcv_daily
    where ticker = %(ticker)s
      and market = %(market)s
    order by date desc
    limit 1
"""

_POSITION_FUND_SQL = """
    select distinct on (ticker)
           revenue_growth_yoy, earnings_growth_yoy,
           gross_margin, pe_ratio, ps_ratio, peg_ratio,
           analyst_target_price, analyst_rating, analyst_count,
           mspr, next_earnings_date, last_earnings_date, fcf_yield
    from stock_fundamentals
    where ticker = %(ticker)s
      and market = %(market)s
    order by ticker, updated_at desc
"""

_META_SQL = """
    select company_name, sector, industry
    from stocks_meta
    where ticker = %(ticker)s and market = %(market)s
"""

# 新闻按代码数组重叠去查（related_tickers 上有 GIN 索引，`&&` 才吃得到）。
# 一条新闻可能挂多只票，所以要把 related_tickers 一起取回来在 Python 里分桶。
_NEWS_SQL = """
    select title, sentiment_label, sentiment_score, published_at,
           related_tickers
    from news
    where related_tickers && %(tickers)s::text[]
      and published_at >= now() - make_interval(days => %(days)s)
    order by published_at desc
"""


# ------------------------------------------------------------------ 取数


def _latest_trading_day(market: str):
    """这个市场最新有日线的那一天；一行数据都没有就返回 None。"""
    row = pg.query_one(_LATEST_DAY_SQL, {"market": market})
    return row["d"] if row else None


def _query_tickers_data(tickers: list[str]) -> list[dict]:
    """按给定的一批代码取行情 + 基本面。美股和 A股分开查，不会串市场。"""
    if not tickers:
        return []

    rows: list[dict] = []
    for market, group in _group_by_market(tickers).items():
        rows += pg.query(_TICKERS_SQL, {"tickers": group, "market": market})

    # 放量的排前面；没有量比数据（新股、停牌）的排最后
    rows.sort(key=lambda r: (r.get("vol_ratio") is None,
                             -(r.get("vol_ratio") or 0), r["ticker"]))
    return rows


def _query_longterm_candidates(market: str = DEFAULT_MARKET) -> list[dict]:
    as_of = _latest_trading_day(market)
    if as_of is None:
        return []
    return pg.query(_LONGTERM_SQL, {"market": market, "as_of": as_of})


def _query_swing_candidates(market: str = DEFAULT_MARKET) -> list[dict]:
    as_of = _latest_trading_day(market)
    if as_of is None:
        return []
    return pg.query(_SWING_SQL, {"market": market, "as_of": as_of})


def _query_position_data(ticker: str, market: str = None) -> dict:
    """一只持仓票的全部素材：行情、基本面、公司信息、近 30 天新闻。"""
    market = (market or _market_of(ticker)).upper()
    args = {"ticker": ticker, "market": market}

    result: dict = {"ticker": ticker, "market": market}

    ohlcv = pg.query_one(_POSITION_OHLCV_SQL, args)
    if ohlcv:
        result.update(ohlcv)
    fundamentals = pg.query_one(_POSITION_FUND_SQL, args)
    if fundamentals:
        result.update(fundamentals)
    meta = pg.query_one(_META_SQL, args)
    if meta:
        result.update(meta)

    result["ticker"] = ticker
    result["news"] = _query_news_for_tickers([ticker], days=30).get(ticker, [])
    return result


def _query_news_for_tickers(tickers: list[str], days: int = 7) -> dict[str, list]:
    """按代码分桶取新闻。一条新闻挂了多只票时，每只票的桶里都会有它。"""
    if not tickers:
        return {}

    rows = pg.query(_NEWS_SQL, {"tickers": list(tickers), "days": int(days)})

    wanted = set(tickers)
    result: dict[str, list] = {t: [] for t in tickers}
    for row in rows:
        item = {
            "headline": row["title"],
            "sentiment_label": row["sentiment_label"],
            "sentiment_score": row["sentiment_score"],
            "published_at": str(row["published_at"]) if row["published_at"] else None,
        }
        for ticker in (row.get("related_tickers") or []):
            if ticker in wanted:
                result[ticker].append(item)
    return result


# ------------------------------------------------------------------ 对外入口


def gather_context(skill_name: str, params: dict = None) -> str:
    """按技能取数、拼成一份 JSON 上下文交给上层。"""
    params = params or {}

    if skill_name in ("longterm_screen", "swing_screen"):
        swing = skill_name == "swing_screen"
        custom_tickers = params.get("tickers")
        if custom_tickers:
            candidates = _query_tickers_data(custom_tickers)
            source = f"screener ({len(custom_tickers)} tickers)"
        elif swing:
            candidates = _query_swing_candidates()
            source = "built-in criteria (above MA20/50, bull alignment, vol>1.2x, RSI 45-75)"
        else:
            candidates = _query_longterm_candidates()
            source = "built-in criteria (growth>10%, margin>30%, PE<60, above MA200)"

        news_days, per_ticker = (7, 3) if swing else (30, 5)
        news = _query_news_for_tickers([c["ticker"] for c in candidates],
                                       days=news_days)
        for candidate in candidates:
            candidate["recent_news"] = news.get(candidate["ticker"], [])[:per_ticker]

        return json.dumps({
            "date": date.today().isoformat(),
            "source": source,
            "candidates_count": len(candidates),
            "candidates": candidates,
        }, default=str, ensure_ascii=False)

    elif skill_name == "position_review":
        positions_input = params.get("positions", [])
        if not positions_input:
            return json.dumps({"error": "positions list is required"})

        all_positions = []
        for position in positions_input:
            ticker = (position.get("ticker") or "").upper()
            if not ticker:
                continue
            data = _query_position_data(ticker, position.get("market"))
            if position.get("cost_basis") is not None:
                data["cost_basis"] = position["cost_basis"]
            if position.get("shares") is not None:
                data["shares"] = position["shares"]
            # 期权异动那边算好的 uw_* 字段原样带过去
            for key, value in position.items():
                if key.startswith("uw_"):
                    data[key] = value
            all_positions.append(data)

        return json.dumps({
            "date": date.today().isoformat(),
            "positions_count": len(all_positions),
            "positions": all_positions,
        }, default=str, ensure_ascii=False)

    else:
        raise ValueError(f"Unknown skill: {skill_name}")
