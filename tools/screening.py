# -*- coding: utf-8 -*-
"""参数化选股器。只读 Postgres，不写任何东西。

三条容易踩的坑，改动的时候别踩回去：

1. **取最新交易日必须走「两段式」：先单独查出那一天，再当常量传进主查询。**
   四种写法在真库上实测过（同样的筛选条件，日线表 275 万行）：

   | 写法 | 顶层代价 | 日线表怎么访问 | 实跑 |
   |---|---|---|---|
   | 相关子查询 ``o.date = (select max(date) ... where ticker = o.ticker)`` | 7,381,726 | 顺序扫 275 万行 | 6750 ms |
   | ``group by market`` 公共表达式 | 181,566 | 并行顺序扫 115 万行 | 375 ms |
   | 按票 LATERAL 倒扫 | 130,744 | 逐票主键倒扫，1.5 万次 | 203 ms |
   | **两段式：先查出最新日再等值比较** | **5,541** | 索引区间扫 | **109 ms** |

   前两种都会退化成全表扫。LATERAL 那种在持仓那边（几十只票）是最快的，
   但选股器要扫全票池，一万五千次倒扫就不划算了。两段式那一句
   ``select max(date) ... where market = %(market)s`` 自己能吃到
   ``idx_ohlcv_daily_market_date`` 索引，代价只有 1.43，多这一次往返完全值。
   顺带一个好处：结果为空时也能告诉上层数据截止到哪天。
2. **基本面表一只票有很多行。** ``stock_fundamentals`` 的主键是
   ``(ticker, updated_at)``，2135 只票摊了 29943 行。直接连表一只票会出来十几
   行，筛选结果全是重复的。必须用 ``distinct on (ticker)`` 取最新那条。
3. **每条查询都要带 market 条件。** 日线表混了美股和 A股，漏了会把 A股串进
   美股结果里。

输出必须是**轻量摘要**：条件是强制的，条数封顶 30，空的伽马墙字段直接不输出。
这是整个项目的核心约束，别为了「顺手多带点」把它弄丢。

## 单位：两个方向，别弄混

这个模块两头都要跟单位打交道，方向是相反的：

- **入参 → SQL**：`min_growth` / `min_margin` 收的是百分比（15 就是 15%），
  库里那两列存的是小数，所以进 SQL 之前除以 100。
- **库 → 返回值**：`revenue_growth_yoy` 这些列查出来是小数，
  交给使用者之前乘 100 换成百分点，跟 `analyze_ticker` 对齐。
  名单见 `_PCT_POINT_FIELDS`。

`pct_chg` 两边都不碰——库里存的它本来就是百分点。
"""
import json
import re

from core import snapshot
from db import pg
from tools.compact import compact_rows

VALID_MARKETS = ("US", "CN")

# 允许的均线周期 -> 日线表里的列名
MA_COLUMNS = {20: "ma_20", 50: "ma_50", 200: "ma_200"}

# 结果条数上限。这是防上下文爆炸的闸门，不要调大。
MAX_LIMIT = 30

# 行业分类的别名。不同数据源叫法不一样：纳斯达克那份用「Health Care」
# 「Consumer Discretionary」，yfinance 用「Healthcare」「Consumer Cyclical」，
# 用户随便写哪种都得认。比较之前会先把字符串压成纯小写字母数字
# （去掉空格和连字符），所以「Health Care」和「Healthcare」天然就相等了，
# 这张表只用来处理换了词的那几组。
_SECTOR_ALIAS_GROUPS = [
    {"technology", "informationtechnology", "tech"},
    {"healthcare", "health"},
    {"consumerdiscretionary", "consumercyclical"},
    {"consumerstaples", "consumerdefensive"},
    {"finance", "financial", "financials", "financialservices"},
    {"telecommunications", "telecom", "communicationservices"},
    {"industrials", "industrial"},
    {"basicmaterials", "materials"},
    {"realestate"},
    {"utilities", "utility"},
]

# 伽马墙（期权做市商的对冲压力位，做波段时可以当天然的止盈止损位）。
# uw_gex_daily 现在只覆盖四十来只大票，绝大多数候选是空的，
# 所以下面会把整组都为空的行直接删掉，不占 token。
_GEX_FIELDS = ("gex_call_wall", "gex_put_wall", "gex_gamma_flip", "gex_gamma_magnet")

# 内部字段，拼完摘要就从每行里删掉，不发给上层。
_INTERNAL_FIELDS = ("_sector_from_meta",)

#: 库里存小数（0.164 = 16.4%），交给使用者之前一律换成百分点。
#:
#: **为什么这里要换**：`screen_stocks` 和 `analyze_ticker` 是同一批对外的 MCP
#: 工具，会在同一次对话里先后出现（先筛股、再对命中的票做体检）。
#: 换算之前，同一只票在两个工具里长这样：
#:
#:     screen_stocks   dist_ma20_pct = 0.0318
#:     analyze_ticker  dist_ma20_pct = 3.18
#:
#: 同名字段差 100 倍，横向一比就必然算错。所以这里对齐到
#: `core/snapshot.py` / `core/fundamental.py` 定下的那条约定：
#: **交到使用者手上的 `_pct` 和增长率/利润率，一律是百分点。**
#:
#: 注意 `pct_chg` 不在这张表里——库里存的它已经是百分点了，再乘一次
#: 会把「当日跌 4.57%」变成「跌 457%」。同一张日线表混了两套单位，
#: 这是上游 tt-data-hub 的既成事实，见 core/snapshot.py 的模块说明。
_PCT_POINT_FIELDS = (
    "dist_ma20_pct", "dist_ma200_pct", "atr_pct",
    "revenue_growth_yoy", "earnings_growth_yoy", "gross_margin",
)

#: 交出去之前截小数，见 `tools/compact.py`。2026-09-03 实测：不截的话
#: 一次 20 只候选里有 139 个 `25.2679996490479` 这种数字，白占 1,411 字符，
#: 占这个工具返回体的 9%。
#:
#: 只列这七个是**故意的**：`_PCT_POINT_FIELDS` 那几个上面刚 round 过 2 位，
#: 再截一遍是白做；而 `pe_ratio`、`market_cap` 这些直接来自
#: `stock_fundamentals`，量级各不相同，没确认过之前不往这里加
#: （量级的坑见 CLAUDE.md 里「比率字段的单位约定」那一节）。
_DIGITS = {"close": 2, "pct_chg": 2, "rsi_14": 1, "vol_ratio": 2,
           "ma_20": 2, "ma_50": 2, "ma_200": 2}


#: 长线排序里，两个增长项各自的封顶值。**这是库里的量级，不是返回值的量级。**
#:
#: 排序发生在 SQL 里，吃的是 `stock_fundamentals` 的裸列，那几列存的是小数
#: （HUT 库里存 0.814，意思是营收增长 81.4%）。返回值那边乘 100 换成百分点是
#: 查询之后在 Python 侧做的（见 _PCT_POINT_FIELDS），排序看不到。所以封顶值
#: 写 1.0 = 增速 100%；写成 100 的话等于没封，写成 0.01 的话所有票会被压平。
#:
#: **为什么要封顶**：三项裸加、不封顶的话，排序会被基数效应统治。
#: 2026-08-28 实测，`screen_type="longterm"` 前八名全是靠单项极端值排进来的：
#: BEP 营收只增长 1.1%，却因为利润增长 4511%（几乎肯定是从接近零的利润基数
#: 恢复）排到第三。一家增速超过 100% 的公司在这个维度上已经顶格了，
#: 再去区分 6007% 和 4671%，分辨的是**基数噪音，不是生意质量**。
#:
#: 封顶**只影响排序，不影响筛选**：`min_growth` 该放行的照样放行。
#: 用户想专门找超高增速的票，用 `min_growth=500` 去筛——那是筛选该管的事。
#:
#: 毛利率不用封顶，它天然就在 0~1 之间。
GROWTH_SORT_CAP = 1.0


# 最新交易日单独查一次，仍然是「两段式」的第一段，别挪进主查询。
#
# 这里查的是【每天有多少只票】而不是 max(date)。2026-09-01 踩到：
# ^VIX 是指数不是股票，盘前就有当天数据，那天全市场只有它一只；
# max(date) 返回那一天之后，主查询按这一天等值筛选，别的票全被排除，
# 美股筛选器只吐出 ^VIX 一只。判断逻辑跟 core/snapshot.py 共用一份，
# 免得两处各写一套、将来只改一边。
LATEST_DAY_SQL = """
select date, count(*) as n
from stock_ohlcv_daily
where market = %(market)s
  and date >= current_date - 30
group by date
order by date desc
"""


def latest_trading_day(market: str):
    """查某个市场最新的那个真正交易日；30 天内一行数据都没有就返回 None。"""
    rows = pg.query(LATEST_DAY_SQL, {"market": market})
    return snapshot.pick_trading_day(rows)


def _normalize_sector(name: str) -> str:
    """压成纯小写字母数字，好让「Health Care」和「Healthcare」相等。"""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _sector_candidates(name: str) -> list[str]:
    """把用户写的板块名展开成所有等价写法（都已归一化）。"""
    key = _normalize_sector(name)
    for group in _SECTOR_ALIAS_GROUPS:
        if key in group:
            return sorted(group)
    return [key]


def build_screen_sql(
    as_of,
    screen_type: str = "longterm",
    market: str = "US",
    sector: str = None,
    min_market_cap: float = None,
    max_pe: float = None,
    min_growth: float = None,
    min_margin: float = None,
    min_rsi: float = None,
    max_rsi: float = None,
    above_ma: int = None,
    limit: int = 20,
) -> tuple[str, dict]:
    """拼出选股 SQL 和参数字典。

    as_of 是这个市场的最新交易日，由 latest_trading_day() 先查好再传进来 ——
    别在这里现算，见模块头那张实测对比表。

    单独抽出来是为了能对它直接跑 EXPLAIN 做性能回归，测试里就是这么用的。
    参数不合法直接抛 ValueError，由 screen_stocks 转成给用户看的错误信息。
    """
    if market not in VALID_MARKETS:
        raise ValueError(f"market 只能是 {' 或 '.join(VALID_MARKETS)}")

    # 占位符是 %s 风格的具名参数，不是 DuckDB 的 ?
    where = ["o.market = %(market)s", "o.date = %(as_of)s"]
    params: dict = {"market": market, "as_of": as_of, "limit": limit}

    if sector:
        # 行业分类来自 stocks_meta.sector；它现在全是空的，
        # 所以临时用 ticker_over20b 兜底，见下面 sector_src 那个连接。
        where.append(
            "regexp_replace(lower(coalesce(m.sector, ts.sector)), "
            "'[^a-z0-9]', '', 'g') = any(%(sectors)s)")
        params["sectors"] = _sector_candidates(sector)
    if min_market_cap is not None:
        where.append("f.market_cap >= %(min_market_cap)s")
        params["min_market_cap"] = min_market_cap * 1e9
    if max_pe is not None:
        # 很多票没有市盈率，把它们一并放过，跟改造前的行为保持一致
        where.append("(f.pe_ratio <= %(max_pe)s or f.pe_ratio is null)")
        params["max_pe"] = max_pe
    if min_growth is not None:
        # **入参是百分比（15 就是 15%），库里那一列存的是小数（0.15），
        # 所以这里必须除以 100 再比。** 别看着「15 变成 0.15」觉得多余就删掉：
        # 删了之后 min_growth=15 会变成「营收增速 1500% 以上」，
        # 筛出来的全是从接近零的基数起步的极端值，而且不报错，
        # 返回的还是一份格式完全正常的结果，非常难发现。
        # 注意这跟返回值那边的换算是**两个方向**：入参百分比→小数进 SQL，
        # 库里小数→百分点出结果（见 _PCT_POINT_FIELDS）。
        where.append("f.revenue_growth_yoy >= %(min_growth)s")
        params["min_growth"] = min_growth / 100.0
    if min_margin is not None:
        # 同上，入参是百分比，gross_margin 库里存小数
        where.append("f.gross_margin >= %(min_margin)s")
        params["min_margin"] = min_margin / 100.0
    if min_rsi is not None:
        where.append("o.rsi_14 >= %(min_rsi)s")
        params["min_rsi"] = min_rsi
    if max_rsi is not None:
        where.append("o.rsi_14 <= %(max_rsi)s")
        params["max_rsi"] = max_rsi
    if above_ma is not None:
        column = MA_COLUMNS.get(above_ma)
        if not column:
            raise ValueError("above_ma 只能是 20、50 或 200")
        where.append(f"o.close > o.{column}")

    if screen_type == "swing":
        # 做波段先看放量
        order_clause = "o.vol_ratio desc nulls last"
    else:
        # 长线看成长和赚钱能力。两个增长项都封顶，见 GROWTH_SORT_CAP。
        order_clause = f"""(
            least(coalesce(f.revenue_growth_yoy, 0), {GROWTH_SORT_CAP}) * 0.4
          + least(coalesce(f.earnings_growth_yoy, 0), {GROWTH_SORT_CAP}) * 0.3
          + coalesce(f.gross_margin, 0) * 0.3
        ) desc nulls last"""

    sql = f"""
        with fund as (
            -- 主键是 (ticker, updated_at)，一只票十几行，取最新那条
            select distinct on (ticker) *
            from stock_fundamentals
            where market = %(market)s or market is null
            order by ticker, updated_at desc
        ),
        sector_src as (
            -- 临时兜底：stocks_meta.sector 几乎全是空的（2026-08-31 实测：
            -- 34683 行里只有 2020 行有值，不到十七分之一，而且全在美股那
            -- 29133 行里；5550 只 A股一行都没有）。
            -- 注意：这段注释里不能出现百分号——psycopg2 会把它当成占位符，
            -- 跟下面的 %(market)s 混在一起直接报「argument formats can't
            -- be mixed」。
            -- 主要有行业分类的地方是 ticker_over20b 这张主选股池表
            -- （约 2164 只，最低市值 20 亿美元 —— 表名里的 over20b 是骗人的，
            --   中位数 85.6 亿，一半以上在 10 亿~100 亿这一档；
            --   其中 2099 只带行业分类）。
            -- 等 tt-data-hub 的 meta_refresh 把 stocks_meta.sector 填上之后，
            -- 这个公共表达式和下面的 coalesce 都可以删掉。
            -- 主键是 (id, ticker)，去个重防止一只票连出多行。
            select distinct on (ticker) ticker, sector
            from ticker_over20b
            order by ticker, id desc
        )
        select
            o.ticker,
            m.company_name,
            coalesce(m.sector, ts.sector) as sector,
            (m.sector is not null) as _sector_from_meta,
            o.market,
            o.close, o.pct_chg, o.rsi_14, o.vol_ratio,
            o.ma_20, o.ma_50, o.ma_200,
            o.dist_ma20_pct,
            round(((o.close - o.ma_200) / nullif(o.ma_200, 0))::numeric, 4)
                as dist_ma200_pct,
            o.atr_pct,
            f.pe_ratio, f.peg_ratio, f.market_cap,
            f.revenue_growth_yoy, f.earnings_growth_yoy, f.gross_margin,
            f.analyst_rating, f.analyst_target_price, f.next_earnings_date,
            g.call_wall    as gex_call_wall,
            g.put_wall     as gex_put_wall,
            g.gamma_flip   as gex_gamma_flip,
            g.gamma_magnet as gex_gamma_magnet
        from stock_ohlcv_daily o
        left join fund f on f.ticker = o.ticker
        left join stocks_meta m on m.ticker = o.ticker
        left join sector_src ts on ts.ticker = o.ticker
        left join uw_gex_daily g
               on g.ticker = o.ticker and g.snapshot_date = o.date
        where {' and '.join(where)}
        order by {order_clause}
        limit %(limit)s
    """
    return sql, params


def screen_stocks(
    screen_type: str = "longterm",
    market: str = "US",
    sector: str = None,
    min_market_cap: float = None,
    max_pe: float = None,
    min_growth: float = None,
    min_margin: float = None,
    min_rsi: float = None,
    max_rsi: float = None,
    above_ma: int = None,
    limit: int = 20,
) -> str:
    """按条件筛股。**至少要给一个筛选条件**，否则拒绝执行。

    screen_type:     'longterm' 长线（按成长和毛利排）| 'swing' 波段（按放量排）
    market:          'US' 美股 | 'CN' A股
    sector:          板块名，比如 'Technology'、'Healthcare'
    min_market_cap:  最小市值，单位十亿美元（10 就是 100 亿）
    max_pe:          最大市盈率（没有市盈率的票会放过）
    min_growth:      最低营收同比增速，百分比（15 就是 15%）
    min_margin:      最低毛利率，百分比
    min_rsi/max_rsi: 相对强弱指标 RSI-14 的上下限（0-100，50 是多空均衡）
    above_ma:        股价必须站上哪条均线（20 / 50 / 200）
    limit:           最多返回几只，默认 20，上限 30

    **返回值的单位**：`dist_ma20_pct`、`dist_ma200_pct`、`atr_pct`、
    `revenue_growth_yoy`、`earnings_growth_yoy`、`gross_margin` 都是**百分点**
    （3.18 就是 3.18%），跟 `analyze_ticker` 返回的同名字段是一个量纲，
    可以直接横向比。`pct_chg` 也是百分点。
    """
    market = (market or "US").upper().strip()

    has_filter = any(v is not None for v in [
        sector, min_market_cap, max_pe, min_growth, min_margin,
        min_rsi, max_rsi, above_ma])
    if not has_filter:
        return json.dumps({
            "error": "至少要给一个筛选条件，否则返回全市场会把上下文撑爆。"
                     "可用条件：sector、min_market_cap、max_pe、min_growth、"
                     "min_margin、min_rsi、max_rsi、above_ma"
        }, ensure_ascii=False)

    limit = max(1, min(int(limit), MAX_LIMIT))

    if market not in VALID_MARKETS:
        return json.dumps(
            {"error": f"market 只能是 {' 或 '.join(VALID_MARKETS)}"},
            ensure_ascii=False)

    # 第一段：先把最新交易日单独查出来（吃索引，代价 1.43）。
    # 这个市场压根没数据就直接短路，连主查询都不用发。
    try:
        as_of = latest_trading_day(market)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    if as_of is None:
        return json.dumps({
            "filters": {"screen_type": screen_type, "market": market},
            "as_of": None,
            "count": 0,
            "sector_source": "无（这批结果没有行业分类）",
            "candidates": [],
            "note": f"{market} 市场的日线表还没有任何数据",
        }, ensure_ascii=False)

    # 第二段：最新交易日当常量传进去做等值比较
    try:
        sql, params = build_screen_sql(
            as_of=as_of,
            screen_type=screen_type, market=market, sector=sector,
            min_market_cap=min_market_cap, max_pe=max_pe,
            min_growth=min_growth, min_margin=min_margin,
            min_rsi=min_rsi, max_rsi=max_rsi, above_ma=above_ma, limit=limit)
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    try:
        rows = pg.query(sql, params)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    from_meta = sum(1 for r in rows if r["_sector_from_meta"])
    from_fallback = sum(
        1 for r in rows if not r["_sector_from_meta"] and r["sector"])

    for row in rows:
        for key in _INTERNAL_FIELDS:
            row.pop(key, None)
        # 比率列换成百分点再交出去，跟 analyze_ticker 对齐（见 _PCT_POINT_FIELDS）。
        # 换算放在这里、不放进 SQL，是因为排序表达式
        # （revenue_growth_yoy*0.4 + earnings_growth_yoy*0.3 + gross_margin*0.3）
        # 直接吃的是库里那几列。在 Python 侧、查完之后再换，排序结果一个字都不变；
        # 在 SQL 的 select 列表里乘 100 也不影响排序，但下一个人很容易顺手把
        # order by 也改了，那就把命中顺序改掉了。
        for key in _PCT_POINT_FIELDS:
            value = row.get(key)
            if value is not None:
                row[key] = round(float(value) * 100, 2)
        # 伽马墙整组为空就整组删掉，省 token
        if all(row.get(k) is None for k in _GEX_FIELDS):
            for key in _GEX_FIELDS:
                row.pop(key, None)

    filters_used = {k: v for k, v in {
        "screen_type": screen_type, "market": market, "sector": sector,
        "min_market_cap": min_market_cap, "max_pe": max_pe,
        "min_growth": min_growth, "min_margin": min_margin,
        "min_rsi": min_rsi, "max_rsi": max_rsi, "above_ma": above_ma,
    }.items() if v is not None}

    result = {
        "filters": filters_used,
        "as_of": as_of.isoformat() if as_of else None,
        "count": len(rows),
        "sector_source": _sector_source_note(from_meta, from_fallback),
        "candidates": compact_rows(rows, _DIGITS),
    }
    return json.dumps(result, ensure_ascii=False, default=str)


def _sector_source_note(from_meta: int, from_fallback: int) -> str:
    """说清楚这批结果的行业分类是哪来的。

    现在 stocks_meta.sector 全是空的，行业分类实际来自 ticker_over20b 这张
    临时兜底表（主选股池，约 2164 只，最低市值 20 亿美元）。这件事必须写在
    结果里，不然上层会误以为板块筛选已经完全可用了。
    """
    if from_meta and from_fallback:
        return ("stocks_meta.sector 与 ticker_over20b 混合"
                f"（{from_meta} 只来自前者，{from_fallback} 只来自后者）")
    if from_meta:
        return "stocks_meta.sector"
    if from_fallback:
        return ("ticker_over20b（临时兜底：stocks_meta.sector 目前全为空。"
                "这张主选股池表约 2164 只、最低市值 20 亿美元，其中 2099 只有"
                "行业分类；等 tt-data-hub 的 meta_refresh 跑过就撤掉）")
    return "无（这批结果没有行业分类）"
