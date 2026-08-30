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
不改），而是不能让 `reacted` 这个字段自称比它实际说的更多——所以每条事件
的 `price_reaction` 里都带一个 `as_of`，写清楚这个涨跌幅到底是哪一天的，
让看的人自己去对这个日期跟消息发布时间差多久。

## 大盘综述不是个股专属催化剂

一条新闻挂的票越多，越可能是「今天大盘怎么样」这类综述，而不是针对某一
只票的消息。实测近 7 天 759 条新闻，95.1% 挂在 6 只票以内；挂 7 只以上的
24 条（3.2%），抽查全是「鹰派表态推高美股加息预期」「英伟达财报带动科技股
大涨」这类大盘综述。这类新闻不从结果里删——大盘什么情况本身有信息量——
但会被标成 `market_wide: true`，并且排序上不许它顶到个股专属消息前面
（`is_market_wide()` / `MARKET_WIDE_TICKER_COUNT`）。

## 三路数据合并

1. `news` 表——有 AI 打好的重要性分级（High/Medium/Low）和情绪。排序靠它。
2. `uw_news`——带 is_major 标记，时效更好。**tickers 列是 jsonb 不是 text[]**，
   要用 `jsonb_exists_any` 匹配，用 `&&` 会直接报类型错误。
3. 价格自己说的话——涨跌幅和量比。

## 一个已知但不归本模块管的坑

`news.related_tickers` 偶尔会把明显不相关的公司标到一起（比如一条跟某公司
毫无关系的产品新闻被顺手标了别的公司代码）。这是上游 tt-data-hub 打标的
问题，本模块判断不了「这条新闻内容上到底跟这只票有没有关系」，只能按
「挂了几只票」这个可观察的信号过滤掉大盘综述那一类噪音，过滤不掉打标本身
就错的那一类。
"""
import json

from core import catalyst, snapshot
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

_SIGNIFICANCE_RANK = {"High": 0, "Medium": 1, "Low": 2}

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

    见模块 docstring「大盘综述不是个股专属催化剂」——阈值和数据来源都写
    在 `MARKET_WIDE_TICKER_COUNT` 的注释里。
    """
    return ticker_count >= MARKET_WIDE_TICKER_COUNT


def _round2(value):
    """价格类数字只保留 2 位小数交出去。行情快照里的浮点数原样透传的话，
    十几位小数（比如 3.9686187944258）直接糊到用户脸上，没有必要。"""
    return None if value is None else round(value, 2)


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


def detect_catalysts(scope: str = "positions", hours: int = 24,
                     min_significance: str = "Medium") -> str:
    """最近出了什么事，跟我的票有没有关系，股价反应过了没有。

    每条消息都会标出「股价已经反应过了没有」——已经涨完的利好不是机会，
    是风险；纹丝不动的利好才有得做。**这个判断只看最新交易日一天的涨跌幅，
    不是消息发布以来的累计涨跌**——`hours` 传得越长这个区别越要紧，所以每条
    事件的 `price_reaction.as_of` 都带着这个涨跌幅具体是哪一天的，自己对一下
    跟 `published_at` 差多久。

    每条消息还会标出是不是「大盘综述」（`market_wide`）——一条新闻同时挂
    很多只票，大概率说的是「今天大盘怎么样」而不是这只票专属的消息，这类
    消息不会被删，但排序上不会顶到个股专属消息前面。

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
                               "message": "持仓和自选都是空的，没什么可看的"},
                              ensure_ascii=False, indent=2)

        wanted = sorted(filter_set) if filter_set else None

        # ---- 第一路：news 表，有重要性分级 ----
        rows = catalyst.news_recent(wanted, hours=hours,
                                    min_significance=min_significance,
                                    limit=120)
        events = []
        for item in rows:
            # 挂了几只票要在过滤成「我关心的票」之前数，不然一条挂了 13 只票
            # 的大盘综述，过滤完只剩自己持仓那 1 只，会被误判成个股专属
            wide = is_market_wide(len(item["tickers"]))
            tickers = [t for t in item["tickers"]
                       if filter_set is None or t in filter_set]
            for code in tickers or ([None] if filter_set is None else []):
                if code is None:
                    continue
                events.append({
                    "ticker": code,
                    "event": item["title"],
                    "url": item["url"],
                    "source": item["source"],
                    "source_table": "news",
                    "sentiment": item["sentiment_label"],
                    "significance": item["significant"],
                    "published_at": item["published_at"],
                    "market_wide": wide,
                })

        # ---- 第二路：uw_news，时效更好但没有分级 ----
        for row in pg.query(_UW_NEWS_SQL, (hours, wanted, wanted, 120)):
            wide = is_market_wide(len(row["tickers"] or []))
            for code in (row["tickers"] or []):
                if filter_set is not None and code not in filter_set:
                    continue
                events.append({
                    "ticker": code,
                    "event": row["title"],
                    "url": None,
                    "source": "Unusual Whales",
                    "source_table": "uw_news",
                    "sentiment": (row["sentiment"] or "").capitalize() or None,
                    "significance": "High" if row["is_major"] else "Medium",
                    "published_at": row["created_at"],
                    "market_wide": wide,
                })

        # ---- 第三路：价格自己说的话 ----
        by_market: dict[str, list[str]] = {}
        for event in events:
            market = markets.get(event["ticker"], "US")
            by_market.setdefault(market, []).append(event["ticker"])
        snaps: dict[str, dict | None] = {}
        for market, codes in by_market.items():
            snaps.update(snapshot.get(sorted(set(codes)), market))

        for event in events:
            snap = snaps.get(event["ticker"])
            event["price_reaction"] = {
                "pct_chg": _round2((snap or {}).get("pct_chg")),
                "vol_ratio": _round2((snap or {}).get("vol_ratio")),
                "atr_pct": _round2((snap or {}).get("atr_pct")),
                "reacted": has_reacted(snap),
                # 上面几个数字具体是哪一天的收盘数据，别让人以为是消息
                # 发布以来累计的涨跌（见模块 docstring）
                "as_of": (snap or {}).get("date"),
            }
            event["held"] = event["ticker"] in held
            event["watched"] = event["ticker"] in watched
            event["_significance_rank"] = _SIGNIFICANCE_RANK.get(
                event["significance"], 3)

        # 先看是不是我的票，再看是不是大盘综述（大盘综述不许顶到个股专属
        # 消息前面），再看重不重要，最后「还没反应」的优先
        events.sort(key=lambda e: (not e["held"], not e["watched"],
                                   e["market_wide"],
                                   e["_significance_rank"],
                                   e["price_reaction"]["reacted"] is True,
                                   e["ticker"]))

        message = ""
        if not events:
            message = (f"最近 {hours} 小时内，"
                       f"{'持仓' if scope == 'positions' else '关注范围内'}"
                       f"没有达到 {min_significance} 级别的消息")

        return json.dumps({
            "scope": scope,
            "hours": hours,
            "min_significance": min_significance,
            "events": events,
            "message": message,
        }, ensure_ascii=False, indent=2, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
