# -*- coding: utf-8 -*-
"""消息面：新闻、财报日、期权异动、内部人买卖、gamma 墙。

## 新闻表的两个坑

1. **`news` 没有 ticker 列。** 代码在 `related_tickers` 里，是个 text[]，
   建了 GIN 索引，所以要用数组重叠运算符 `&&` 匹配，写 `= any()` 用不上索引。
2. **没有 headline 列**，标题在 `title`。中文标题在 `title_cn`，可能为空。

重要性分级 `significant` 是 text，值只有 High / Medium / Low 三种，
是 tt-data-hub 那边 AI 打好的。UW 的实时新闻接口**没有分级**（只有一个
is_major 布尔值），所以「按重要性排序」这件事只有库里的数据能做。

## 财报日和市值是短线的合法例外

它俩存在 stock_fundamentals 里，但短波段需要它们避雷和过流动性门槛
（设计文档第三节）。所以 `earnings_for()` **只 select ticker /
next_earnings_date / last_earnings_date / market_cap 这四列**，
不写 `select *`，免得把整行基本面顺手带进短线链路。

## 期权异动有两条路

- `options_flow()` 查库里的 `flow_alerts_archive`。全市场扫描用它，快。
- `options_flow_live()` 调 UW 实时接口，取不到就自动退回库里的数据，
  并把 `source` 标成 `db`。持仓盯盘和个股分析用它——库里的期权数据一天
  只灌两次，拿它盯盘名不副实。
"""
import datetime

from core import uw
from db import pg

#: 重要性从高到低。min_significance='Medium' 的意思是 High + Medium。
SIGNIFICANCE_ORDER = ("High", "Medium", "Low")

#: 一条新闻挂到这么多只票以上，就不是个股新闻而是大盘综述。
#: 实测近 7 天 759 条新闻，95.1% 挂在 6 只票以内；挂 7 只以上的 24 条
#: （3.2%）从多到少抽查全是「今天大盘涨跌」这类综述，例如：
#:   [13只] Stocks Fall as Hawkish Warsh Boosts Fed Rate Hike Chances
#:   [10只] Stock Indices Settle Higher as Nvidia Earnings Spur a Tech Rally
#:   [ 8只] Stocks open on mixed footing ahead of Warsh's Jackson Hole speech
#:
#: **这个数字只在这里定义一次**（评审 I3）。五个工具、同一批新闻数据、
#: 同一天、同一个用户，判据没道理不同；早期只有 `detect_catalysts` 和
#: `watch_positions` 做了这个过滤，真实后果是 NVDA 靠一条挂 7 只票的
#: 《马斯克加速燃气轮机生产之路伴随污染难题》在 `scan_swing` 里拿到
#: 消息面满分 20/20，ORCL 靠一条挂 8 只票的《大型科技公司AI支出规模远超
#: 预期》在 `review_positions` 里把紧急度从 20 抬到 65。
MARKET_WIDE_TICKER_COUNT = 7

#: 内部人交易代码：P = 公开市场买入，S = 卖出。
#: A（授予）、M（期权行权）、F（代扣税）、D（处置）不是主动买卖，是噪音。
#: 实测近 30 天：S 4697 条、M 1981 条、A 1870 条、F 996 条、P 770 条。
_INSIDER_CODES = {"P": "buy", "S": "sell"}

#: 新闻分散在两张表里，两张表都要查。
#:
#:   * `news`    —— 美股为主，**不是这个仓库写的**，也不是 tt-data-hub 写的，
#:                  写它的是另一套系统（带大模型打的情绪分和重要性分级）。
#:   * `cn_news` —— A股，tt-data-hub 的 `cn_news` 任务写的（2026-09-01 新增）。
#:
#: 为什么必须合起来查：实测 `news` 表 82387 条里带 A股代码的只有 218 条
#: （0.26%），只查它等于 A股没有消息面。两张表的列是刻意对齐的，
#: 所以这里 `union all` 之后再过滤——**先合并再过滤，参数个数跟原来一样**，
#: 而且 Postgres 会把条件下推到两个分支各自走索引。
#:
#: `id` 两张表各自自增、可能撞号，但全链路没有任何地方拿 id 当去重键
#: （去重看的是 url 和标题），所以撞了也不影响。
#: 两张新闻表共用的列清单，写一次防止两边漂移。
COLS = """id, title, title_cn, url, published_at, source,
       sentiment_label, sentiment_score, significant, related_tickers"""

_NEWS_UNION = """
    select {cols}
    from news
    union all
    select {cols}
    from cn_news
""".format(cols=COLS)

_NEWS_SQL = f"""
select {COLS}
from ({_NEWS_UNION}) n
where related_tickers && %s::text[]
  and published_at >= now() - make_interval(days => %s)
  and significant = any(%s)
order by published_at desc
limit %s
"""

_NEWS_RECENT_SQL = f"""
select {COLS}
from ({_NEWS_UNION}) n
where published_at >= now() - make_interval(hours => %s)
  and significant = any(%s)
  and (%s::text[] is null or related_tickers && %s::text[])
order by published_at desc
limit %s
"""

# 只取这四列。别改成 select *，见模块注释。
_EARNINGS_SQL = """
select distinct on (ticker)
       ticker, next_earnings_date, last_earnings_date, market_cap
from stock_fundamentals
where ticker = any(%s)
  and market = %s
order by ticker, updated_at desc
"""

_FLOW_SQL = """
select ticker,
       count(*) as alert_count,
       sum(case when opt_type = 'call' then ask_prem else bid_prem end)
           as bullish_premium,
       sum(case when opt_type = 'call' then bid_prem else ask_prem end)
           as bearish_premium,
       max(total_premium) as biggest_premium,
       max(day) as last_day
from flow_alerts_archive
where ticker = any(%s)
  and day >= %s
group by ticker
"""

_INSIDER_SQL = """
select ticker,
       payload->>'transaction_code' as code,
       count(*) as n,
       sum((payload->>'amount')::numeric) as amount
from uw_insider_tx
where ticker = any(%s)
  and filing_date >= %s
  and payload->>'transaction_code' = any(%s)
group by ticker, payload->>'transaction_code'
"""

# 国会议员申报的交易。金额是个区间字符串（"$50,001 - $100,000"），
# 原样带出来——把它折算成一个数会假装精确。
_CONGRESS_SQL = """
select ticker, transaction_date, filed_at_date,
       payload->>'name' as member,
       payload->>'member_type' as member_type,
       payload->>'txn_type' as txn_type,
       payload->>'amounts' as amounts
from uw_congress
where ticker = any(%s)
  and transaction_date >= %s
order by transaction_date desc
limit %s
"""

_GEX_SQL = """
select distinct on (ticker)
       ticker, snapshot_date, call_wall, put_wall, gamma_flip, gamma_magnet
from uw_gex_daily
where ticker = any(%s)
order by ticker, snapshot_date desc
"""


def _clean(tickers) -> list[str]:
    return [t.upper().strip() for t in (tickers or []) if t and t.strip()]


def _levels(min_significance: str) -> list[str]:
    """'Medium' -> ['High', 'Medium']。认不出的当 Medium。"""
    try:
        cut = SIGNIFICANCE_ORDER.index(min_significance)
    except ValueError:
        cut = 1
    return list(SIGNIFICANCE_ORDER[:cut + 1])


def is_market_wide(item_or_count) -> bool:
    """这条新闻是不是「大盘/行业综述」而不是个股专属消息。

    可以传一条 `_shape_news()` 出来的新闻（读它的 `tickers` 数组）、一条
    `slim_news()` 压过的新闻（读它的 `ticker_count`），也可以直接传一个
    **过滤前**的原始票数。

    传票数时必须是过滤前的——一条挂了 13 只票的大盘综述，如果按「过滤后
    只剩我持仓那 1 只」去数，几乎总是数不到阈值，会被误判成个股专属
    （`tools/detect_catalysts.py` 模块 docstring 里有这个坑的详细说明和
    专门的回归测试）。

    **两种形态都要认**，是因为压过的那条已经没有 `tickers` 数组了：
    只认数组的话，`len(None or [])` 一路算出 0，压过的新闻会全部被判成
    「个股专属」——最该被降权的大盘综述反而成了打分依据。
    """
    if isinstance(item_or_count, dict):
        if item_or_count.get("ticker_count") is not None:
            count = item_or_count["ticker_count"]
        else:
            count = len(item_or_count.get("tickers") or [])
    else:
        count = item_or_count or 0
    return count >= MARKET_WIDE_TICKER_COUNT


def first_stock_specific(items: list[dict] | None) -> dict | None:
    """一串新闻里第一条**不是大盘综述**的。全是综述或者压根没有就返回 None。

    「先按每票只留 1 条、再判断是不是综述」是错的顺序：那唯一的一条恰好
    是综述时，这只票当天就一条个股消息都没有了，哪怕它后面紧跟着一条真
    正的个股专属消息（评审 M9 在 `watch_positions` 里抓到过）。所以调用方
    要多拉几条候选，交给这个函数挑。
    """
    for item in items or []:
        if not is_market_wide(item):
            return item
    return None


def _shape_news(row: dict) -> dict:
    return {
        "title": row.get("title_cn") or row.get("title"),
        "title_en": row.get("title"),
        "url": row.get("url"),
        "published_at": row.get("published_at"),
        "source": row.get("source"),
        "sentiment_label": row.get("sentiment_label"),
        "sentiment_score": row.get("sentiment_score"),
        "significant": row.get("significant"),
        "tickers": row.get("related_tickers") or [],
    }


def slim_news(item: dict) -> dict:
    """一条新闻压成「交出去」的最小形态。

    `_shape_news` 给的是内部用的完整形态（分组要用 `tickers` 数组、
    双语标题两个都在）。真正交到 Claude 手上的时候，那几个字段是纯浪费：

      * `title_en` 是同一句话的另一个语种，标题已经有一个了；
      * `tickers` 那一长串代码——**有用的是「这条消息挂了几只票」这个数字**
        （挂太多说明是大盘综述，不是这只票的专属消息），代码本身没用，
        而且一条新闻挂在两只票下面时会被整份复制两遍；
      * `published_at` 精确到微秒带时区，看新闻精确到分钟就够。

    2026-09-03 实测：一条新闻从 422 字符压到 218，`analyze_ticker` 的
    8 条新闻从 3,377 压到约 1,700。

    这套压法本来只长在 `tools/review_positions.py` 里，`analyze_ticker`
    没有——同一件事在一个工具里做了、另一个没做。提到这里两边共用。
    """
    # 压两次也要得到同样的结果：第二次进来时 `tickers` 已经没了，
    # 只看数组的话计数会被清零，`market_wide` 跟着翻脸。
    count = item.get("ticker_count")
    if count is None:
        count = len(item.get("tickers") or [])
    published = item.get("published_at")
    return {
        "title": item.get("title"),
        "url": item.get("url"),
        # `2026-09-03 02:11:07.238171+00:00` → `2026-09-03 02:11`
        "published_at": str(published)[:16] if published else None,
        "sentiment_label": item.get("sentiment_label"),
        "significant": item.get("significant"),
        # 这条消息一共挂了几只票，以及够不够多到该当成大盘综述看
        "ticker_count": count,
        "market_wide": is_market_wide(count),
    }


def news_for(tickers: list[str], days: int = 7,
             min_significance: str = "Medium",
             per_ticker: int = 3) -> dict[str, list[dict]]:
    """按票分组的近期新闻，每票最多 per_ticker 条。"""
    tickers = _clean(tickers)
    if not tickers:
        return {}

    # 一次查回来再在 Python 里分组。一条新闻可能同时关联几只票，
    # 按票分别查会把同一条新闻查很多遍。
    rows = pg.query(_NEWS_SQL, (tickers, days, _levels(min_significance),
                                per_ticker * len(tickers) * 4))
    out: dict[str, list[dict]] = {t: [] for t in tickers}
    wanted = set(tickers)
    for row in rows:
        item = _shape_news(row)
        for ticker in wanted & set(item["tickers"]):
            if len(out[ticker]) < per_ticker:
                out[ticker].append(item)
    return out


def news_recent(tickers: list[str] | None = None, hours: int = 24,
                min_significance: str = "Medium",
                limit: int = 60) -> list[dict]:
    """最近 N 小时的新闻，按时间倒序。tickers 为 None 就是全市场。"""
    tickers = _clean(tickers) if tickers else None
    rows = pg.query(_NEWS_RECENT_SQL,
                    (hours, _levels(min_significance), tickers, tickers, limit))
    return [_shape_news(row) for row in rows]


def earnings_for(tickers: list[str], market: str) -> dict[str, dict | None]:
    """下次财报日还有几天。没有数据的票值是 None。"""
    tickers = _clean(tickers)
    if not tickers:
        return {}

    today = datetime.date.today()
    found = {}
    for row in pg.query(_EARNINGS_SQL, (tickers, market.upper())):
        next_date = row.get("next_earnings_date")
        days_until = None
        if isinstance(next_date, datetime.date):
            days_until = (next_date - today).days
        found[row["ticker"]] = {
            "next_earnings_date": next_date,
            "last_earnings_date": row.get("last_earnings_date"),
            "days_until": days_until,
            "market_cap": row.get("market_cap"),
        }
    return {t: found.get(t) for t in tickers}


def options_flow(tickers: list[str], days: int = 5) -> dict[str, dict]:
    """库里的期权异动汇总。没记录的票返回零值，不返回 None。

    看多看空怎么分：在卖价成交说明买方主动，在买价成交说明卖方主动。
    买看涨=看多、卖看涨=看空、买看跌=看空、卖看跌=看多。
    """
    tickers = _clean(tickers)
    if not tickers:
        return {}

    since = datetime.date.today() - datetime.timedelta(days=days)
    found = {}
    for row in pg.query(_FLOW_SQL, (tickers, since)):
        bullish = float(row.get("bullish_premium") or 0)
        bearish = float(row.get("bearish_premium") or 0)
        found[row["ticker"]] = {
            "bullish_premium": round(bullish, 2),
            "bearish_premium": round(bearish, 2),
            "net_premium": round(bullish - bearish, 2),
            "biggest_premium": float(row.get("biggest_premium") or 0),
            "alert_count": int(row.get("alert_count") or 0),
            "last_day": row.get("last_day"),
            "source": "db",
        }
    empty = {"bullish_premium": 0.0, "bearish_premium": 0.0,
             "net_premium": 0.0, "biggest_premium": 0.0,
             "alert_count": 0, "last_day": None, "source": "db"}
    return {t: found.get(t, dict(empty)) for t in tickers}


def options_flow_live(tickers: list[str]) -> dict[str, dict]:
    """实时期权异动。取不到就退回库里的数据，并把 source 标成 db。

    库里一天只灌两次，做盯盘要实时的；但实时接口 3.6 秒一只票，
    而且会超时，所以永远要有退路。
    """
    tickers = _clean(tickers)
    if not tickers:
        return {}

    fallback = options_flow(tickers)
    if not uw.available():
        return fallback

    live = uw.flow_alerts_many(tickers)
    out = {}
    for ticker in tickers:
        rows = live.get(ticker)
        if rows is None:
            # 实时接口没取到（超时、报错、没配密钥），退回库里的
            out[ticker] = fallback[ticker]
        else:
            # 实时取到了。空数组也算取到——「实时查过，这只票近期没有异动」
            # 是个结论，不是缺数据，不能拿旧数据顶掉再说成缓存
            summary = uw.summarize_flow(rows)
            summary["last_day"] = None   # 实时数据没有「库里最后一天」这回事
            out[ticker] = summary
    return out


def insider_for(tickers: list[str], days: int = 90) -> dict[str, dict]:
    """内部人在公开市场的买卖。授予、行权、代扣税不算，见模块注释。"""
    tickers = _clean(tickers)
    if not tickers:
        return {}

    since = datetime.date.today() - datetime.timedelta(days=days)
    out = {t: {"buy_count": 0, "sell_count": 0,
               "buy_amount": 0.0, "sell_amount": 0.0, "net_amount": 0.0}
           for t in tickers}
    for row in pg.query(_INSIDER_SQL, (tickers, since, list(_INSIDER_CODES))):
        entry = out.get(row["ticker"])
        if entry is None:
            continue
        side = _INSIDER_CODES[row["code"]]
        amount = float(row.get("amount") or 0)
        entry[f"{side}_count"] = int(row["n"])
        entry[f"{side}_amount"] = round(amount, 2)
    for entry in out.values():
        entry["net_amount"] = round(entry["buy_amount"] - entry["sell_amount"], 2)
    return out


def congress_for(tickers: list[str],
                 days: int = 180) -> dict[str, list[dict]]:
    """国会议员申报的这几只票的交易。申报有滞后，所以窗口默认放到半年。"""
    tickers = _clean(tickers)
    if not tickers:
        return {}
    since = datetime.date.today() - datetime.timedelta(days=days)
    out: dict[str, list[dict]] = {t: [] for t in tickers}
    # 上限跟票数挂钩。写死一个数的话，票一多就会有票被挤掉，
    # 而调用方收到空列表分不清是真没有还是被截断了。
    limit = max(40, len(tickers) * 10)
    for row in pg.query(_CONGRESS_SQL, (tickers, since, limit)):
        out.setdefault(row["ticker"], []).append({
            "transaction_date": row["transaction_date"],
            "filed_at_date": row.get("filed_at_date"),
            "member": row.get("member"),
            "member_type": row.get("member_type"),
            "txn_type": row.get("txn_type"),
            "amounts": row.get("amounts"),
        })
    return out


def gamma_walls(tickers: list[str]) -> dict[str, dict | None]:
    """期权 gamma 墙（短期的压力位和支撑位）。

    `uw_gex_daily` 实测只覆盖 41 只大盘股，绝大多数票会是 None。
    这是加分项，缺了不致命，但要明写缺失，不能编。
    """
    tickers = _clean(tickers)
    if not tickers:
        return {}
    found = {row["ticker"]: {
        "snapshot_date": row["snapshot_date"],
        "call_wall": row.get("call_wall"),
        "put_wall": row.get("put_wall"),
        "gamma_flip": row.get("gamma_flip"),
        "gamma_magnet": row.get("gamma_magnet"),
    } for row in pg.query(_GEX_SQL, (tickers,))}
    return {t: found.get(t) for t in tickers}
