# -*- coding: utf-8 -*-
"""工具 4：突发消息侦测——出了什么事，跟我有没有关系。

## 最关键的一项判断：股价反应过了没有

已经涨完的利好不是机会，是风险。一条「某公司拿下大订单」的新闻，如果股价
今天已经跳了 9%，这时候进去是接盘；如果股价纹丝不动，才是机会。

判据：当日涨跌幅绝对值 > 2 倍该票平时的日波动（用 atr_pct 做基准），
或者量比 > 2，就算已经反应过了。**没有行情数据时返回 None 而不是 False**——
说「不知道」比说「还没反应」诚实，后者会骗人去追。

**这个判据只看「最新交易日」涨了/跌了多少，不是「消息发布以来累计」涨跌了
多少。** `hours` 传得越长（比如 7 天），这个区别越要紧：一条 3 天前的消息
可能当天就让股价跳了一截，之后又回落，只看最新一天的涨跌会显得「还没
反应」，但行情其实早演完了。这不是本模块的判据错了（判据是设计上定死的，
不改），而是不能让 `reacted` 这个字段自称比它实际说的更多——所以每只票的
`price_reaction` 里都带一个 `as_of`，写清楚这个涨跌幅到底是哪一天的。

## 一条新闻就是一个事件，不按票扇出（修复轮 2 / C1）

一条新闻可能同时挂了好几只票。**早期实现按票扇出**——一条新闻挂 3 只持仓
就变成 3 条一模一样的事件，标题原样复制 3 份——这既是表述错误（这条新闻
明明是一件事，不是三件事），也直接顶到了项目最硬的一条规矩：**工具只返回
算好的结论，不把原始表倒给 Claude**。实测最坏情况（`watchlist, 336h,
min_significance="Low"`）按票扇出后是 254 KB、377 条事件、只有 239 个
唯一标题——同一个标题最多复制了 8 份。

现在一条新闻**只生成一个事件**，事件带一个 `tickers` 列表（本次 scope
范围内命中的票），`price_reaction` 也跟着改成按票组织的小字典
（`{ticker: {...}}`），不会因为去重就丢了「这条消息对每只票分别意味着
什么」这个信息。`events` 数量另外有硬上限，见 `MAX_EVENTS` 的注释。

## 大盘综述不是个股专属催化剂

一条新闻挂的票越多，越可能是「今天大盘怎么样」这类综述，而不是针对某一
只票的消息。实测近 7 天 759 条新闻，95.1% 挂在 6 只票以内；挂 7 只以上的
24 条（3.2%），抽查全是「鹰派表态推高美股加息预期」「英伟达财报带动科技股
大涨」这类大盘综述。这类新闻不从结果里删——大盘什么情况本身有信息量——
但会被标成 `market_wide: true`，并且排序上不许它顶到个股专属消息前面
（`is_market_wide()` / `MARKET_WIDE_TICKER_COUNT`）。

**挂了几只票要在按 scope 过滤之前数**（`is_market_wide()` 吃的是过滤前的
原始票数）。一条挂了 13 只票的大盘综述，如果按「过滤后只剩几只落在我的
持仓里」去数，几乎总是数不到阈值，会被误判成个股专属——这个坑修复轮 2
的评审专门测过（把数票数挪到过滤之后，判定大盘综述的三条测试全部通过，
但语义已经错了），所以额外补了一条只在 `scope="positions"` 下才测得出来
的用例（`test_market_wide_counts_tickers_before_filtering_by_scope`）。

## 三路数据合并，互相独立，一路挂了不连累另一路（修复轮 2 / I3）

1. `news` 表——有 AI 打好的重要性分级（High/Medium/Low）和情绪。排序靠它。
2. `uw_news`——带 is_major 标记，时效更好。**tickers 列是 jsonb 不是 text[]**，
   要用 `jsonb_exists_any` 匹配，用 `&&` 会直接报类型错误。
3. 价格自己说的话——涨跌幅和量比，按市场分批查 `snapshot.get()`。

这三路各自包在自己的 `try/except` 里：任何一路抛异常都只把这一路记成
`data_gaps`（写明是哪一路挂的），另外两路照常给结果，跟同目录其它工具
（`review_positions.py`、`scan_swing.py`、`analyze_ticker.py`）的约定
一致——不能因为一路数据源抽风就把整个工具的结论都丢了。

行情新不新鲜也在这里报：每个涉及到的市场都查一次
`regime.freshness()`，陈旧、采集失败、采集只跑一半都写进 `warnings`；
每只票自己的快照如果 `stale`，也在它的 `price_reaction` 里标出来。

## 一个已知但不归本模块管的坑

`news.related_tickers` 偶尔会把明显不相关的公司标到一起（比如一条跟某公司
毫无关系的产品新闻被顺手标了别的公司代码）。这是上游 tt-data-hub 打标的
问题，本模块判断不了「这条新闻内容上到底跟这只票有没有关系」，只能按
「挂了几只票」这个可观察的信号过滤掉大盘综述那一类噪音，过滤不掉打标本身
就错的那一类。
"""
import json

from core import catalyst, regime, snapshot
from db import pg

SCOPES = ("positions", "watchlist", "market")

#: 涨跌幅超过平时波动的几倍就算「已经反应」
REACTED_ATR_MULT = 2.0
#: 量比超过这个数也算
REACTED_VOL_RATIO = 2.0

#: 一条新闻挂到这么多只票以上，就不是个股新闻而是大盘综述。
#: 实测近 7 天 759 条新闻，95.1% 挂在 6 只票以内；挂 7 只以上的 24 条
#: 从多到少抽查全是「今天大盘涨跌」这类综述，例如：
#:   [13只] Stocks Fall as Hawkish Warsh Boosts Fed Rate Hike Chances
#:   [10只] Stock Indices Settle Higher as Nvidia Earnings Spur a Tech Rally
#:   [ 8只] Stocks open on mixed footing ahead of Warsh's Jackson Hole speech
MARKET_WIDE_TICKER_COUNT = 7

#: `events` 最多返回这么多条（按排序后的顺序截断，最相关的排在前面）。
#: 一条新闻改成一个事件（见模块 docstring「一条新闻就是一个事件」）之后，
#: 实测四种调用去重后、加这道上限之前的大小（2026-08-30）：
#:   positions, 24h（默认）           7 条，6.2 KB
#:   positions, 168h                122 条，94.8 KB
#:   market, 72h, Low                115 条，115.7 KB
#:   watchlist, 336h, Low（最坏情况） 240 条，197.2 KB
#: 上限定成 40：四种调用封顶后分别是 6.2 / 30.9 / 47.1 / 43.5 KB，
#: 最坏情况被摁到了几十 KB 量级。按排序后的顺序截断，留下来的正是
#: 「持仓/自选、个股专属、重要、还没反应」这四层过滤下来最该看的那批，
#: 被砍掉的是同一批里排序最靠后的。
MAX_EVENTS = 40

_SIGNIFICANCE_RANK = {"High": 0, "Medium": 1, "Low": 2}

#: 两路新闻各自最多从数据库拉多少条原始行（去重、按 scope 过滤之前）。
_NEWS_FETCH_LIMIT = 120
_UW_NEWS_FETCH_LIMIT = 120

_POSITIONS_SQL = "select ticker, market from user_positions"

_WATCHLIST_SQL = """
select w.ticker, coalesce(m.market, 'US') as market
from user_watchlist w
left join stocks_meta m on m.ticker = w.ticker
"""

# tickers 是 jsonb。jsonb_exists_any 是 ?| 运算符的函数写法——
# 用函数是为了不在 SQL 里出现问号，那会跟 psycopg2 的占位符和
# 我们的规矩测试打架。
_UW_NEWS_SQL = """
select created_at, title, sentiment, is_major, tickers
from uw_news
where created_at >= now() - make_interval(hours => %s)
  and (%s::text[] is null or jsonb_exists_any(tickers, %s::text[]))
order by created_at desc
limit %s
"""


def has_reacted(snap: dict | None) -> bool | None:
    """股价对消息反应过了没有。数据不全时返回 None，不猜。

    只看快照里「最新交易日」这一天的涨跌幅，不是消息发布以来的累计涨跌——
    调用方如果把 hours 传得很长，要自己意识到这个区别（模块 docstring
    里有详细说明）。
    """
    if not snap:
        return None
    pct_chg = snap.get("pct_chg")
    atr_pct = snap.get("atr_pct")
    vol_ratio = snap.get("vol_ratio")
    if pct_chg is None and vol_ratio is None:
        return None
    if vol_ratio is not None and vol_ratio > REACTED_VOL_RATIO:
        return True
    if pct_chg is None or atr_pct is None or atr_pct <= 0:
        return None if vol_ratio is None else False
    return abs(pct_chg) > REACTED_ATR_MULT * atr_pct


def is_market_wide(ticker_count: int) -> bool:
    """一条新闻挂的票达到这个数，就判定成大盘综述而不是个股新闻。

    调用方必须传**过滤前**的原始票数，见模块 docstring 里的坑。
    """
    return ticker_count >= MARKET_WIDE_TICKER_COUNT


def _round2(value):
    """价格类数字只保留 2 位小数交出去。行情快照里的浮点数原样透传的话，
    十几位小数（比如 3.9686187944258）直接糊到用户脸上，没有必要。"""
    return None if value is None else round(value, 2)


def _clean_tickers(raw, filter_set: set | None) -> list[str]:
    """从一条新闻挂的票里，挑出「非空、落在本次 scope 范围内」的那些，
    排好序，保证输出确定。空字符串这种脏数据在这里被过滤掉。"""
    return sorted({
        t for t in (raw or [])
        if t and t.strip() and (filter_set is None or t in filter_set)
    })


def _scope_tickers(scope: str) -> tuple[set[str], set[str], dict[str, str]]:
    """返回 (持仓集合, 自选集合, 代码 -> 市场)。"""
    held, watched, markets = set(), set(), {}
    for row in pg.query(_POSITIONS_SQL):
        held.add(row["ticker"])
        markets[row["ticker"]] = row["market"]
    if scope in ("watchlist", "market"):
        for row in pg.query(_WATCHLIST_SQL):
            watched.add(row["ticker"])
            markets.setdefault(row["ticker"], row["market"])
    return held, watched, markets


def _sort_key(event: dict) -> tuple:
    """先看是不是我的票，再看是不是大盘综述（大盘综述不许顶到个股专属
    消息前面），再看重不重要，最后「还没反应」的优先。

    重要性和「反应过没有」都不存进事件字典里——那是排序用的内部辅助值，
    不该交给 Claude（修复轮 2 / C1：`_significance_rank` 曾经就是这么
    泄漏出去的）。"""
    reactions = event["price_reaction"].values()
    all_reacted = bool(reactions) and all(r["reacted"] is True for r in reactions)
    significance_rank = _SIGNIFICANCE_RANK.get(event["significance"], 3)
    return (not event["held"], not event["watched"], event["market_wide"],
            significance_rank, all_reacted,
            event["tickers"][0] if event["tickers"] else "")


def detect_catalysts(scope: str = "positions", hours: int = 24,
                     min_significance: str = "Medium") -> str:
    """最近出了什么事，跟我的票有没有关系，股价反应过了没有。

    一条新闻就是一个事件（`tickers` 列出命中的票），不会因为一条新闻同时
    挂了好几只持仓就被说成好几件事。每只票会分别标出「股价已经反应过了
    没有」——已经涨完的利好不是机会，是风险；纹丝不动的利好才有得做。
    **这个判断只看最新交易日一天的涨跌幅，不是消息发布以来的累计涨跌**，
    每只票的 `price_reaction.as_of` 都带着这个涨跌幅具体是哪一天的。

    每条消息还会标出是不是「大盘综述」（`market_wide`）——一条新闻同时挂
    很多只票，大概率说的是「今天大盘怎么样」而不是这只票专属的消息，这类
    消息不会被删，但排序上不会顶到个股专属消息前面。

    `events` 数量有上限（`MAX_EVENTS`），返回体里的 `event_count` /
    `returned_count` / `truncated` 如实写明总共匹配了多少条、实际返回了
    多少条，不会默默截断。

    scope: positions 只看持仓 / watchlist 持仓加自选 / market 全市场
    hours: 往回看几小时，默认 24
    min_significance: High 只看重大 / Medium 重大和中等（默认）/ Low 全要
    """
    try:
        scope = (scope or "positions").lower()
        if scope not in SCOPES:
            return json.dumps({"error": f"scope 只能是 positions / watchlist / "
                                        f"market，收到 {scope}"},
                              ensure_ascii=False)
        hours = max(1, min(int(hours), 24 * 14))

        held, watched, markets = _scope_tickers(scope)
        if scope == "positions":
            filter_set = held
        elif scope == "watchlist":
            filter_set = held | watched
        else:
            filter_set = None

        if filter_set is not None and not filter_set:
            return json.dumps({"scope": scope, "hours": hours, "events": [],
                               "event_count": 0, "returned_count": 0,
                               "truncated": False, "warnings": [],
                               "data_gaps": [],
                               "message": "持仓和自选都是空的，没什么可看的"},
                              ensure_ascii=False, indent=2)

        wanted = sorted(filter_set) if filter_set else None
        gaps: list[str] = []
        warnings: list[str] = []
        events: list[dict] = []

        # ---- 第一路：news 表，有重要性分级。挂了不连累另外两路 ----
        try:
            rows = catalyst.news_recent(wanted, hours=hours,
                                        min_significance=min_significance,
                                        limit=_NEWS_FETCH_LIMIT)
        except Exception as exc:
            gaps.append(f"news 表新闻拉取失败：{exc}")
            rows = []

        for item in rows:
            # 挂了几只票要在过滤成「我关心的票」之前数，不然一条挂了 13
            # 只票的大盘综述，过滤完只剩自己持仓那 1 只，会被误判成个股
            # 专属（模块 docstring 有详细说明，这条判断有专门的测试守着）
            wide = is_market_wide(len(item["tickers"]))
            tickers = _clean_tickers(item["tickers"], filter_set)
            if not tickers:
                continue
            events.append({
                "tickers": tickers,
                "event": item["title"],
                "url": item["url"],
                "source": item["source"],
                "source_table": "news",
                "sentiment": item["sentiment_label"],
                "significance": item["significant"],
                "published_at": item["published_at"],
                "market_wide": wide,
            })

        # ---- 第二路：uw_news，时效更好但没有分级。挂了不连累另外两路 ----
        try:
            uw_rows = pg.query(_UW_NEWS_SQL,
                               (hours, wanted, wanted, _UW_NEWS_FETCH_LIMIT))
        except Exception as exc:
            gaps.append(f"uw_news 拉取失败：{exc}")
            uw_rows = []

        for row in uw_rows:
            raw_tickers = row["tickers"] or []
            wide = is_market_wide(len(raw_tickers))
            tickers = _clean_tickers(raw_tickers, filter_set)
            if not tickers:
                continue
            events.append({
                "tickers": tickers,
                "event": row["title"],
                "url": None,
                "source": "Unusual Whales",
                "source_table": "uw_news",
                "sentiment": (row["sentiment"] or "").capitalize() or None,
                "significance": "High" if row["is_major"] else "Medium",
                "published_at": row["created_at"],
                "market_wide": wide,
            })

        # ---- 行情新不新鲜：跟事件命不命中新闻无关，只要这个市场里有票
        # 在本次 scope 范围内（持仓/自选），就查一次。一个市场查挂了
        # 不连累另一个市场 ----
        for market in sorted(set(markets.values())):
            try:
                fresh = regime.freshness(market)
            except Exception:
                fresh = None
            if fresh:
                if fresh["stale"]:
                    warnings.append(
                        f"{market} 行情最新只到 {fresh['latest_trading_day']}，"
                        f"距今 {fresh['days_since']} 天")
                if fresh["failed_jobs"]:
                    warnings.append(
                        f"{market} 有采集任务失败：{fresh['failed_jobs']}")
                if fresh["partial_jobs"]:
                    warnings.append(
                        f"{market} 有采集任务只跑了一半（数据写了，某个可选源"
                        f"没取到）：{fresh['partial_jobs']}")

        # ---- 第三路：价格自己说的话。按市场分批查，一个市场挂了
        # 不连累另一个市场，也不连累前两路已经拿到的新闻 ----
        by_market: dict[str, list[str]] = {}
        for event in events:
            for ticker in event["tickers"]:
                market = markets.get(ticker, "US")
                by_market.setdefault(market, []).append(ticker)

        snaps: dict[str, dict | None] = {}
        for market, codes in by_market.items():
            try:
                snaps.update(snapshot.get(sorted(set(codes)), market))
            except Exception as exc:
                gaps.append(f"{market} 行情快照拉取失败：{exc}")

        for event in events:
            reactions = {}
            for ticker in event["tickers"]:
                snap = snaps.get(ticker)
                reactions[ticker] = {
                    "pct_chg": _round2((snap or {}).get("pct_chg")),
                    "vol_ratio": _round2((snap or {}).get("vol_ratio")),
                    "atr_pct": _round2((snap or {}).get("atr_pct")),
                    "reacted": has_reacted(snap),
                    # 上面几个数字具体是哪一天的收盘数据，别让人以为是消息
                    # 发布以来累计的涨跌（见模块 docstring）
                    "as_of": (snap or {}).get("date"),
                    "stale": bool((snap or {}).get("stale")),
                }
            event["price_reaction"] = reactions
            event["held"] = any(t in held for t in event["tickers"])
            event["watched"] = any(t in watched for t in event["tickers"])

        events.sort(key=_sort_key)

        event_count = len(events)
        returned = events[:MAX_EVENTS]
        truncated = event_count > len(returned)

        message = ""
        if not returned:
            message = (f"最近 {hours} 小时内，"
                       f"{'持仓' if scope == 'positions' else '关注范围内'}"
                       f"没有达到 {min_significance} 级别的消息")

        return json.dumps({
            "scope": scope,
            "hours": hours,
            "min_significance": min_significance,
            "events": returned,
            "event_count": event_count,
            "returned_count": len(returned),
            "truncated": truncated,
            "warnings": warnings,
            "data_gaps": sorted(set(gaps)),
            "message": message,
        }, ensure_ascii=False, indent=2, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
