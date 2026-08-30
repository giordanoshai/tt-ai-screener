# -*- coding: utf-8 -*-
"""工具 5：持仓盯盘——我的持仓上有没有需要立刻处理的信号。

## 没事就说没事

不触发就返回空的告警列表加一句「N 只持仓，无异常」。给一堆看着热闹但不用管
的东西，等于没有盯盘——用户会开始忽略它，然后真出事的那天也一起忽略了。

所以门槛定得高：期权异动要单笔溢价 ≥ 50 万美元，或者 score 进这只票近 30 天的
前 10%（实测 NVDA 和 AMZN 的 90 分位都是 60 分）。

## 期权异动按票汇总，不按笔数扇出

**这条是拿真实数据实测出来的，不是猜的。** 用现有 6 只美股持仓、默认
`hours=24`（往回看 2 天）实测：仅 AMZN 一只票就有 39 笔交易过了「单笔
≥ 50 万美元」这条线，8 天窗口下 AMZN 一只票涨到 54 笔。如果一笔对应一条
告警，光期权这一路就能刷出小一百条，跟同目录 `detect_catalysts.py` 那次
「一条新闻按票数扇出成 N 条」是同一类问题，也正撞上这个工具自己的第一条
设计原则——「没事就说没事」，不是「有点动静就说」。

所以这里改成**一只票一条**：把过线的交易按票分组，数出看涨看跌各几笔、
各自的溢价合计、以及金额最大的那一笔，合成一条告警。哪边溢价合计大就按
哪边定调（`big_bullish_flow` / `big_bearish_flow`）。这样「AMZN 这两天
期权很热」只出现一次，而不是同一个结论用 39 种措辞重复 39 遍。

UW 实时警报（`/api/alerts`）同理：同一只票在 100 条里触发好几条自定义规则
时，也合并成一条，把命中的规则名都列出来，不按规则条数扇出。

## 每条告警说清三件事

发生了什么、对我的仓位意味着什么、建议怎么处理。只说「NVDA 出现大额看跌期权」
是没用的——用户还得自己算这对手上 20 股意味着什么。

## UW 实时警报的坑

`/api/alerts` 返回的 `symbol` 是**期权合约代码**（`NVDA261002P00190000`），
不是股票代码；标的藏在 `meta.underlying_symbol` 里。而且 100 条里有 14 条是
`potus_schedule` 这种噪音，`symbol` 直接是 null。拿 symbol 直接跟持仓比对
会一条都对不上，必须先用 uw.underlying_of() 解出标的。

这个接口不像 `flow_alerts_archive` 那样直接给 `sentiment` 字段，**不代表
它没有方向可判断**——2026-08-31 实测：`meta` 里有 `ask_volume` / `bid_volume`
（这批合约成交量里，多少手是在卖价成交、多少手是在买价成交），`symbol` 里
的 OCC 合约代码能解出这是看涨还是看跌期权（到期日后紧跟着的那个字符，
`C`=call、`P`=put）。这两样凑在一起，跟 `core/uw.split_premium()` 判断
`flow_alerts_archive` 里期权异动方向用的是**同一套逻辑**：在卖价成交=
买方主动，在买价成交=卖方主动；买看涨=看多、卖看涨=看空、买看跌=看空、
卖看跌=看多。所以这里直接复用 `split_premium()`，不用自己另造一套判断。

早期写法图省事，不管三七二十一把这条来源的告警一律标成 `big_bullish_flow`——
这不是「少给了信息」，是**告诉用户一个可能错的方向**：2026-08-30 实测命中
的两条都是 `Stealth Accumulation Scanner`，解出来一看，AMZN/GOOGL 那两条
的合约都是 **PUT**，而且 `ask_volume` 远大于 `bid_volume`（买方在追价买入
看跌期权），按上面那套逻辑读出来是**看空**，原来的写法却报成了「看多」——
方向整个反了。现在按合约代码解方向，解不出来（比如合约代码格式不对、或者
`ask_volume`/`bid_volume` 缺失/相等分不出谁占优）就**不猜**，改成「有大额
异动但方向判断不了」，并把这一路的这个空缺写进 `data_gaps`。宁可说不知道，
不许猜一个方向说得像知道——那样错的代价，是用户按「有资金在看多」去加仓，
实际那批钱在看空。

## 实时接口挂了也要能用，某一路挂了也不能连累别的路

价格触发（跌破均线、跌破止损、创新低）和财报临近都是从库里算的，
不依赖任何外部接口。UW 那两路（期权异动、实时警报）取不到就在
`checked_sources` 里少一项，其余照常给——**每一路各自 try/except**，
出错写进 `data_gaps`，不会因为一路挂了把另一路已经拿到的结果一起丢掉
（同目录 `review_positions.py` / `detect_catalysts.py` / `scan_swing.py`
都是这个约定）。

行情新不新鲜、采集任务有没有失败或者只跑一半，也在这里查一次并写进
`warnings`——跟前面几个工具的约定一致，「行情是新的但某个采集任务挂了」
这种情况必须无条件报出来，不能挂在「行情陈旧」判断下面变成潜台词。

## 止损告警：浮盈还是浮亏、跌破多深，都要说清楚

`position_stop()` 的止损位是「成本-2×ATR」和「20 日均线」取高的那个——均线
在成本上方时说明有浮盈，止损会抬到均线护住。这带来两个坑，都是拿真实持仓
（AMZN/GOOGL/MP/NVO/ORCL/RKLB）实测跑出来才发现的（2026-08-30/31）：

**坑一：浮盈还是浮亏，文案要跟真实盈亏方向一致。** 早期写法不分青红皂白
一律印「浮亏 {数字}」，AMZN 当天明明浮盈 204 美元（成本 246、现价
266.43），被印成「浮亏 204 USD」——把赚钱的仓位说成在亏钱。现在按盈亏
正负分别措辞，数字一律取绝对值（不再出现「浮亏 -184」这种双重负号）。

**坑二：「止损位是均线、仓位还在浮盈」跟「止损是成本减 ATR、仓位已经亏钱」
是两件性质完全不同的事，不能用同一个严重度。** 6 只真实持仓里：

```
        跌破幅度   盈亏      止损依据
AMZN    0.17%     浮盈 8.3%   ma_20
NVO     1.69%     浮盈 3.7%   ma_20
GOOGL   1.06%     浮亏 5.0%   ma_20
MP     10.86%    浮亏 19.0%   成本 - 2×ATR
ORCL   13.80%    浮亏 19.8%   成本 - 2×ATR
RKLB   13.11%    浮亏 23.3%   成本 - 2×ATR
```

AMZN 只是轻触均线、账面还赚 8.3%，跟 MP 那种已经深跌穿成本止损 10.86%、
浮亏近两成的仓位，读起来不该是同一个「马上处理」。所以现在拆成两个 kind：

- `stop_breached`：**仓位已经浮亏**时触发。这才是真正意义上的「止损被
  击穿」——这笔交易已经错了，按纪律该走。严重度按跌破幅度分两档（深/浅），
  界值是按上面这批真实数据的分布定的：跌破 ≥8% 判「深度击穿」（MP/ORCL/
  RKLB 都在 10.86%~13.80%，明显该是最高档）；跌破 <8% 判「浅破但仍是
  真亏损」（GOOGL 只跌破 1.06%，但已经是浮亏，不该跟浮盈的那两只同档，
  又不该跟深度击穿的那三只同档）。
- `trailing_stop_touched`：**仓位还在浮盈**、只是碰到了用来护盈利的移动
  止损线，这是「该考虑落袋一部分利润了」，不是「这笔错了」。同样按跌破
  幅度分两档，但整体严重度远低于 `stop_breached`（AMZN/NVO 分别是
  0.17%/1.69%，都落在低档）。

跟 `review_positions.py` 用的是同一套词汇习惯——那个工具已经区分了
`stop_breached`（真击穿，严重度最高）和 `stop_breached_recovering`
（已经站回均线，严重度降档）两档；这里的 `trailing_stop_touched` 是同一个
「破位不等于都一样紧急」的思路在持仓盯盘这个场景下的对应物（触发条件不
完全一样——那边看的是「有没有站回均线」，这边看的是「止损依据是不是均线
且账面还在赚钱」——两个工具面对的判断条件本来就不同，不能照抄同一个
kind 名，但严重度该拉开档位、破位不该无脑扣最高分，这个原则是一致的）。

**坑三：止损依据就是 20 日均线时，不要再单独发一条「跌破 20 日均线」的
告警**——那是同一件事。`position_stop()` 的 `basis` 只有两种取值：`ma_20`
或 `成本 - 2×ATR`（没有 `ma_50` 这个选项），所以这个去重只会跟
`below_ma20` 冲突，不会影响 `below_ma50`（那是独立的中期趋势信号）。

## 突发消息：中性情绪不算「正面」，大盘综述不算个股专属

**中性情绪不该被硬塞进「正面」。** 早期写法是「负面否则正面」的二分法，
2026-08-30 实测抓到一个真实反例：一条 `sentiment_label=Neutral`、同时挂了
8 只票（NVDA/MRVL/GOOGL/AMZN/AAPL/MSFT/META/TSLA）的科技股综述，被报成
AMZN、GOOGL 的「重大正面消息」。现在补上 `neutral_news` 这一档：情绪中性
的重大消息照样告诉用户「出了件大事」，但不替它编一个方向。

**挂了很多只票的新闻是大盘综述，不是这只票的专属消息，直接不发这条。**
`tools/detect_catalysts.py` 已经用真实分布定过这条线：实测近 7 天 759 条
新闻，95.1% 挂在 6 只票以内，挂 7 只以上的抽查全是「大盘/行业综述」而非
个股专属消息（`MARKET_WIDE_TICKER_COUNT = 7`）。这里直接复用同一个数字和
同一条实测依据，不凭空另定一个阈值——上面那条 8 只票的综述，用这条线一过，
自然就被过滤掉了，不需要额外再判断情绪。跟 `detect_catalysts.py` 不同的
是：那个工具的定位是「出了什么事」，大盘综述本身有信息量，标出来但保留；
这个工具的定位是「我这只仓位现在要不要处理」，一条泛大盘的宏观新闻不构成
针对某一个具体仓位的处理理由，所以这里选择直接不发，而不是发出来再标注。

## 「阶段新低」窗口跟文字要对得上

早期实现里 SQL 窗口是 40 个自然日（约 27~28 个交易日），但告警文字曾经
写的是「20 日新低」——文字和窗口对不上，用户会按字面理解成 20 个交易日。
现在把 SQL 改成真正的「最近 20 根日线」（用 `row_number()` 按票分组取最近
20 行，不是拍一个自然日窗口去近似），文字也相应写成「创 20 个交易日新
低」，跟 `core/snapshot.py` 里 `high_20`（20 个交易日高点）的口径保持一致。

## 返回体大小有上限

`alerts` 按严重度排序后最多返回 `MAX_ALERTS` 条，`alert_count` /
`returned_count` / `truncated` 如实写明总共命中多少条、实际返回多少条，
不会默默截断。加了按票汇总之后，正常情况下用不到这个上限（几只持仓顶多
出几条告警），但持仓只数会变、UW 规则会变，这道上限是留给将来的安全网。
"""
import json

from core import catalyst, cn_signals, regime, scoring, snapshot, uw
from db import pg

#: 单笔期权溢价到这个数（美元）才值得打扰用户
BIG_PREMIUM = 500_000

#: 严重度。数字只用来排序。
#: `stop_breached` / `trailing_stop_touched` 不在这里——它们的严重度按跌破
#: 幅度动态算，见 `_stop_alert_severity()`，不是查这张表。
SEVERITY = {
    "below_ma50": 80,
    "earnings_soon": 75,
    "big_bearish_flow": 70,
    "cn_outflow": 65,
    "below_ma20": 60,
    "new_low": 60,
    "lhb": 50,
    "big_bullish_flow": 45,
}

#: 财报还有几个交易日算「临近」（5 个交易日 ≈ 7 个自然日）
EARNINGS_SOON_DAYS = 7

#: alerts 最多返回这么多条。见模块 docstring「返回体大小有上限」。
MAX_ALERTS = 50

#: 一条新闻挂到这么多只票以上，就不是个股新闻而是大盘综述，直接不发。
#: 数字和依据照抄 `tools/detect_catalysts.py` 的 `MARKET_WIDE_TICKER_COUNT`
#: ——同一批新闻数据，同一天，同一个用户会同时用到两个工具，判据没道理不同。
MARKET_WIDE_TICKER_COUNT = 7

#: 止损告警严重度分档的跌破幅度界值。2026-08-30/31 用真实 6 只持仓的分布定的
#: （见模块 docstring「止损告警」一节的表格）：MP/ORCL/RKLB 跌破 10.86%~
#: 13.80%（且都是浮亏），明显该是最高档；GOOGL 只跌破 1.06%但也是浮亏；
#: AMZN/NVO 跌破 0.17%/1.69%但还在浮盈。
STOP_BREACH_DEEP_PCT = 8.0
TRAILING_TOUCH_DEEP_PCT = 3.0

_POSITIONS_SQL = """
select ticker, market, avg_cost, shares,
       coalesce(currency,
                case market when 'CN' then 'CNY' else 'USD' end) as currency
from user_positions
order by ticker
"""

_FLOW_RECENT_SQL = """
select ticker, opt_type, total_premium, score, sentiment, day,
       strike, expiry
from flow_alerts_archive
where ticker = any(%s)
  and day >= current_date - %s
order by total_premium desc
limit 200
"""

_FLOW_P90_SQL = """
select ticker,
       percentile_cont(0.9) within group (order by score) as p90
from flow_alerts_archive
where ticker = any(%s)
  and day >= current_date - 30
group by ticker
"""

# 真正的「最近 20 根日线」（按票分组、按日期倒排取前 20 行），不是拍一个
# 自然日窗口去近似——这条踩过坑，见模块 docstring「阶段新低窗口」一节。
_NEW_LOW_SQL = """
select ticker, min(low) as low_20
from (
    select ticker, low,
           row_number() over (partition by ticker order by date desc) as rn
    from stock_ohlcv_daily
    where ticker = any(%s)
      and market = %s
) ranked
where rn <= 20
group by ticker
"""


def is_big_flow(row: dict, p90: float | None) -> bool:
    """这笔期权异动够不够大，值不值得报。"""
    premium = row.get("total_premium")
    if premium is not None and float(premium) >= BIG_PREMIUM:
        return True
    score = row.get("score")
    if score is not None and p90 is not None and float(score) >= float(p90):
        return True
    return False


def _stop_alert_severity(kind: str, breach_depth_pct: float) -> int:
    """止损相关告警的严重度：跌破幅度越深越严重，且「止损被击穿」
    （已经在浮亏）整体比「移动止损被触及」（还在浮盈）更严重。

    界值和分档依据见模块 docstring「止损告警」一节——用 2026-08-30/31
    六只真实持仓的分布定的，不是拍脑袋。
    """
    depth = abs(breach_depth_pct)
    if kind == "stop_breached":
        return 95 if depth >= STOP_BREACH_DEEP_PCT else 75
    # trailing_stop_touched
    return 55 if depth >= TRAILING_TOUCH_DEEP_PCT else 40


def _uw_live_direction(item: dict) -> str | None:
    """从一条 `/api/alerts` 实时警报里判断方向：看涨还是看跌。

    这个接口不像 `flow_alerts_archive` 那样直接给 `sentiment` 字段，但给了
    期权合约代码（能解出 call/put）和 `meta` 里的 `ask_volume`/`bid_volume`
    （在卖价成交的量 vs 在买价成交的量）。用跟 `core/uw.split_premium()`
    判断 `flow_alerts_archive` 期权异动方向**同一套逻辑**：在卖价成交=
    买方主动，在买价成交=卖方主动；买看涨=看多、卖看涨=看空、买看跌=看空、
    卖看跌=看多。

    解不出合约类型、或者拿不到/两边打平分不出谁占优时返回 None——
    不知道就说不知道，不猜一个方向出来（见模块 docstring）。
    """
    symbol = item.get("symbol")
    # OCC 合约代码：股票代码 + 6 位到期日 + C/P + 8 位行权价，
    # 后缀（到期日+C/P+行权价）固定 15 位，第 7 个字符（索引 6）是 C/P。
    if not symbol or len(symbol) <= 15:
        return None
    tail = symbol[-15:]
    kind = {"C": "call", "P": "put"}.get(tail[6])
    if kind is None:
        return None

    meta = item.get("meta") or {}
    ask_vol, bid_vol = meta.get("ask_volume"), meta.get("bid_volume")
    if ask_vol is None or bid_vol is None:
        return None
    try:
        ask_vol, bid_vol = float(ask_vol), float(bid_vol)
    except (TypeError, ValueError):
        return None

    up, down = uw.split_premium(kind, ask_vol, bid_vol)
    if up == down:
        return None
    return "bullish" if up > down else "bearish"


def _alert(ticker, kind, what, means, action, severity: int | None = None) -> dict:
    return {
        "ticker": ticker,
        "kind": kind,
        "what_happened": what,
        "what_it_means": means,
        "suggested_action": action,
        "severity": severity if severity is not None else SEVERITY.get(kind, 30),
    }


def _freshness_warnings(market: str, fresh: dict) -> list[str]:
    """把 `regime.freshness()` 的结论翻成人话警告。拆成纯函数是为了能测——
    真实数据哪天行情不陈旧、采集任务都正常，端到端测这条逻辑就会全绿，
    测不出「陈旧警告被删掉了」这种改坏（`detect_catalysts.py` 踩过这个坑）。
    """
    out = []
    if fresh["stale"]:
        out.append(f"{market} 行情最新只到 {fresh['latest_trading_day']}，"
                   f"距今 {fresh['days_since']} 天")
    if fresh["failed_jobs"]:
        out.append(f"{market} 有采集任务失败：{fresh['failed_jobs']}")
    if fresh["partial_jobs"]:
        out.append(f"{market} 有采集任务只跑了一半（数据写了，某个可选源"
                   f"没取到）：{fresh['partial_jobs']}")
    return out


def watch_positions(hours: int = 24) -> str:
    """我的持仓上有没有需要立刻处理的信号。

    四路一起看：价格触发（跌破均线、跌破止损、创 20 个交易日新低）、
    财报临近、期权异动（A股换成主力资金和龙虎榜）、突发消息，外加 UW
    实时警报流里命中持仓的部分。

    **没事就说没事**——不触发就返回空列表加一句话，不制造噪音。
    每条告警都会说清楚：发生了什么、对你的仓位意味着什么、建议怎么处理。
    同一只票的多笔期权异动会合并成一条，不会因为一只票交易活跃就刷屏。

    止损相关的告警分两种、严重度按跌破幅度分档，不是固定分：`stop_breached`
    是仓位已经浮亏时的真实破位（跌破越深分越高）；`trailing_stop_touched`
    是仓位还在浮盈、只是碰到了用来护盈利的移动止损线（整体严重度远低于
    前者）。止损依据本来就是 20 日均线时，不会重复再发一条「跌破 20 日
    均线」。详见模块 docstring「止损告警」一节。

    价格触发和财报临近不依赖任何外部接口，UW 那两路（期权异动、实时警报）
    挂了不连累其它路，会在 `checked_sources` 里少一项、在 `data_gaps`
    里写明原因。行情陈旧、采集任务失败或只跑一半会写进 `warnings`。

    hours: 消息和期权异动往回看几小时，默认 24
    """
    try:
        hours = max(1, min(int(hours), 24 * 7))
        rows = pg.query(_POSITIONS_SQL)
        if not rows:
            return json.dumps({"alerts": [], "checked": 0,
                               "checked_sources": ["price"],
                               "hours": hours, "warnings": [],
                               "data_gaps": [], "alert_count": 0,
                               "returned_count": 0, "truncated": False,
                               "message": "没有持仓，没什么可盯的"},
                              ensure_ascii=False, indent=2)

        by_market: dict[str, list[str]] = {}
        for row in rows:
            by_market.setdefault(row["market"], []).append(row["ticker"])
        cost_of = {r["ticker"]: float(r["avg_cost"] or 0) for r in rows}
        shares_of = {r["ticker"]: float(r["shares"] or 0) for r in rows}
        currency_of = {r["ticker"]: r["currency"] for r in rows}
        all_tickers = [r["ticker"] for r in rows]

        snaps: dict[str, dict | None] = {}
        earnings: dict[str, dict | None] = {}
        lows: dict[str, float] = {}
        gaps: list[str] = []
        warnings: list[str] = []

        # ---- 每个涉及到的市场：行情快照、财报日、阶段新低、新鲜度。
        # 一个市场里某一项挂了，不连累这个市场的其它项，也不连累另一个市场 ----
        for market, tickers in by_market.items():
            try:
                snaps.update(snapshot.get(tickers, market))
            except Exception as exc:
                gaps.append(f"{market} 行情快照拉取失败：{exc}")

            try:
                earnings.update(catalyst.earnings_for(tickers, market))
            except Exception as exc:
                gaps.append(f"{market} 财报日拉取失败：{exc}")

            try:
                for low_row in pg.query(_NEW_LOW_SQL, (tickers, market)):
                    lows[low_row["ticker"]] = float(low_row["low_20"])
            except Exception as exc:
                gaps.append(f"{market} 阶段新低查询失败：{exc}")

            try:
                fresh = regime.freshness(market)
            except Exception as exc:
                fresh = None
                gaps.append(f"{market} 行情新鲜度检测失败：{exc}")
            if fresh:
                warnings.extend(_freshness_warnings(market, fresh))

        alerts: list[dict] = []
        sources = ["price"]

        # ---- 第一路：价格触发。不依赖任何外部接口，只用上面已经拿到的数据 ----
        for ticker, snap in snaps.items():
            if not snap:
                continue
            close = snap["close"]
            currency = currency_of[ticker]
            stop = scoring.position_stop(cost_of[ticker], snap)
            # 止损依据就是 20 日均线、且这条已经发过告警时，跌破 20 日均线
            # 是同一件事，不再单独重复一条（模块 docstring「坑三」）
            suppress_below_ma20 = False
            if stop.get("breached"):
                pnl = (close - cost_of[ticker]) * shares_of[ticker]
                depth = abs(stop.get("distance_pct") or 0.0)
                # 均线在成本上方时止损本来就抬到了均线上（护浮盈），这时候
                # 破位是「浮盈还在，但保护线没了」，不是「亏钱了」——两者
                # 对交易者的含义完全不同，必须用不同的 kind、不同的措辞和
                # 不同的严重度区分开（模块 docstring「止损告警」一节）
                if pnl >= 0:
                    kind = "trailing_stop_touched"
                    pnl_text = (f"这笔仓位现在还是浮盈 {pnl:.0f} {currency}，"
                               "但价格已经跌破了用来锁定利润的止损线")
                    action = ("考虑先落袋一部分利润，或者把止损重新定在一个"
                              "你能接受的位置；这不是「这笔交易错了」，"
                              "不必马上清仓")
                else:
                    kind = "stop_breached"
                    pnl_text = (f"这笔仓位当前浮亏 {abs(pnl):.0f} {currency}，"
                               "已经跌出了当初设的容错范围")
                    action = ("按纪律该减仓或清掉。要留就得说清楚新的止损"
                              "放哪、为什么这次不一样")
                alerts.append(_alert(
                    ticker, kind,
                    f"现价 {close} 跌破了建议止损位 {stop['suggested_stop']}"
                    f"（依据：{stop['basis']}，跌破幅度 {depth:.2f}%）",
                    pnl_text, action,
                    severity=_stop_alert_severity(kind, depth)))
                if stop.get("basis") == "ma_20":
                    suppress_below_ma20 = True
            if snap.get("above_ma50") is False:
                alerts.append(_alert(
                    ticker, "below_ma50",
                    f"跌破 50 日均线（{snap['ma_50']}）",
                    "中期趋势转弱了，波段的多头依据没了",
                    "波段仓位考虑离场；要留就当成中线仓，重新定止损"))
            elif snap.get("above_ma20") is False and not suppress_below_ma20:
                alerts.append(_alert(
                    ticker, "below_ma20",
                    f"跌破 20 日均线（{snap['ma_20']}）",
                    "短期动能转弱，但中期趋势还在",
                    "先减一部分观察，等站回均线再补"))
            low_20 = lows.get(ticker)
            if low_20 is not None and close is not None and close <= low_20:
                alerts.append(_alert(
                    ticker, "new_low",
                    f"创 20 个交易日新低（{close}）",
                    "没有买盘接，下跌还在延续",
                    "别在下跌途中摊平成本，先等企稳信号"))

        # ---- 第二路：财报临近。同样只用已经拿到的数据 ----
        for ticker, info in earnings.items():
            days_until = (info or {}).get("days_until")
            if days_until is not None and 0 <= days_until <= EARNINGS_SOON_DAYS:
                alerts.append(_alert(
                    ticker, "earnings_soon",
                    f"{days_until} 天后财报（{info['next_earnings_date']}）",
                    "财报是掷骰子，一晚上可能跳 10% 以上，方向不可预测",
                    "波段仓位建议财报前减到能承受的量，或者干脆先出来"))

        # ---- 第三路：期权异动（美股）/ 主力资金和龙虎榜（A股）----
        us_tickers = by_market.get("US", [])
        if us_tickers:
            try:
                sources.append("options_flow")
                days = max(1, hours // 24 + 1)
                p90 = {r["ticker"]: r["p90"]
                       for r in pg.query(_FLOW_P90_SQL, (us_tickers,))}
                big_by_ticker: dict[str, list[dict]] = {}
                for row in pg.query(_FLOW_RECENT_SQL, (us_tickers, days)):
                    if is_big_flow(row, p90.get(row["ticker"])):
                        big_by_ticker.setdefault(row["ticker"], []).append(row)

                # 按票汇总，不按笔数扇出——见模块 docstring
                for ticker, big_rows in big_by_ticker.items():
                    bullish = [r for r in big_rows if r["sentiment"] == "bullish"]
                    bearish = [r for r in big_rows if r["sentiment"] == "bearish"]
                    bullish_sum = sum(float(r["total_premium"] or 0)
                                      for r in bullish)
                    bearish_sum = sum(float(r["total_premium"] or 0)
                                      for r in bearish)
                    biggest = max(big_rows,
                                 key=lambda r: float(r["total_premium"] or 0))
                    net_bearish = bearish_sum > bullish_sum
                    alerts.append(_alert(
                        ticker,
                        "big_bearish_flow" if net_bearish else "big_bullish_flow",
                        f"近 {days} 天有 {len(big_rows)} 笔大额期权交易：看涨 "
                        f"{len(bullish)} 笔合计 {bullish_sum / 1e4:.0f} 万美元、"
                        f"看跌 {len(bearish)} 笔合计 {bearish_sum / 1e4:.0f} "
                        f"万美元，最大一笔 {float(biggest['total_premium']) / 1e4:.0f} "
                        f"万美元（{biggest['opt_type']}，行权价 {biggest['strike']}，"
                        f"到期 {biggest['expiry']}）",
                        "有大资金在这个方向上集中押注，" +
                        ("跟你的多头仓位是反的" if net_bearish else "跟你的方向一致"),
                        "别只凭这一条动手，配合价格看——"
                        "价格同时跌破均线才是真信号" if net_bearish
                        else "可以作为继续持有的旁证，不构成加仓理由"))
            except Exception as exc:
                gaps.append(f"期权异动拉取失败：{exc}")

        cn_tickers = by_market.get("CN", [])
        if cn_tickers:
            try:
                sources.append("cn_fund_flow")
                for ticker, flow in cn_signals.fund_flow(cn_tickers).items():
                    if not flow:
                        continue
                    net_5d = flow.get("net_5d") or 0
                    if net_5d < -100_000_000:
                        alerts.append(_alert(
                            ticker, "cn_outflow",
                            f"主力 5 日净流出 {abs(net_5d) / 1e8:.2f} 亿",
                            "机构资金在持续出货，散户接盘的行情很难走远",
                            "考虑减仓，至少不要在这个位置加"))
            except Exception as exc:
                gaps.append(f"A股主力资金拉取失败：{exc}")

            try:
                for ticker, records in cn_signals.lhb(cn_tickers, days=5).items():
                    if records:
                        alerts.append(_alert(
                            ticker, "lhb",
                            f"近 5 天上过龙虎榜（{records[0]['reason']}）",
                            "游资在这只票上进出，波动会明显放大",
                            "把止损放宽一点或者减小仓位，别被日内波动扫掉"))
            except Exception as exc:
                gaps.append(f"龙虎榜拉取失败：{exc}")

        # ---- 第四路：突发消息。中性情绪不算正面；挂票数够多的大盘综述
        # 不算这只仓位的专属消息，直接不发（模块 docstring 有详细说明）----
        try:
            news = catalyst.news_for(all_tickers, days=max(1, hours // 24),
                                     min_significance="High", per_ticker=1)
            if news:
                sources.append("news")
            for ticker, items in news.items():
                for item in items:
                    if len(item.get("tickers") or []) >= MARKET_WIDE_TICKER_COUNT:
                        continue  # 大盘综述，不是这只票的专属消息
                    label = (item.get("sentiment_label") or "").lower()
                    if label == "negative":
                        kind, tag, means = ("bad_news", "负面",
                                            "重大消息通常会改变这只票未来"
                                            "几天的走势方向")
                    elif label == "positive":
                        kind, tag, means = ("good_news", "正面",
                                            "重大消息通常会改变这只票未来"
                                            "几天的走势方向")
                    else:
                        # 情绪中性（或缺失）：告诉用户出了件大事，但不替
                        # 它编一个正面/负面的方向（模块 docstring 有真实
                        # 案例：一条中性、挂 8 只票的综述曾被报成「正面」）
                        kind, tag, means = ("neutral_news", "",
                                            "这条消息的情绪判断是中性的，"
                                            "没有明确指向利好还是利空，"
                                            "自己看一眼原文再判断")
                    alerts.append(_alert(
                        ticker, kind,
                        f"重大{tag}消息：{item['title']}" if tag
                        else f"重大消息（情绪中性）：{item['title']}",
                        means,
                        "先看股价反应过了没有——已经跳完的消息不用再动作；"
                        "还没反应的才需要决定"))
        except Exception as exc:
            gaps.append(f"消息拉取失败：{exc}")

        # ---- UW 实时警报：解出标的、解方向，同一只票多条规则合并成一条 ----
        try:
            if uw.available():
                live = uw.alerts(limit=100)
                if live is not None:
                    sources.append("uw_alerts")
                    held = set(all_tickers)
                    # ticker -> {"bullish": [规则名...], "bearish": [...],
                    #            "unknown": [...]}
                    grouped: dict[str, dict[str, list[str]]] = {}
                    for item in live:
                        underlying = uw.underlying_of(item)
                        if not underlying or underlying not in held:
                            continue
                        name = item.get("name") or item.get("noti_type") or "?"
                        direction = _uw_live_direction(item) or "unknown"
                        bucket = grouped.setdefault(
                            underlying, {"bullish": [], "bearish": [],
                                        "unknown": []})
                        if name not in bucket[direction]:
                            bucket[direction].append(name)

                    for ticker, buckets in grouped.items():
                        bullish, bearish, unknown = (buckets["bullish"],
                                                     buckets["bearish"],
                                                     buckets["unknown"])
                        if bullish and not bearish:
                            kind, names = "big_bullish_flow", bullish
                        elif bearish and not bullish:
                            kind, names = "big_bearish_flow", bearish
                        else:
                            # 方向不明（拿不到 ask/bid 量、解不出合约类型），
                            # 或者同一只票里既有看多又有看空的规则，方向
                            # 说不清——不猜，如实说不知道
                            kind = None
                            names = bullish + bearish + unknown
                            gaps.append(
                                f"{ticker} 的 UW 实时警报方向判断不了"
                                f"（{len(unknown)} 条缺方向数据"
                                + (f"，{len(bullish)} 条看多、"
                                   f"{len(bearish)} 条看空，方向不统一"
                                   if bullish or bearish else "") + "）")

                        if kind:
                            alerts.append(_alert(
                                ticker, kind,
                                f"实时警报触发：{'、'.join(names)}",
                                "你配置的扫描规则在这只持仓上触发了，"
                                "解出来的期权方向是"
                                + ("看多" if kind == "big_bullish_flow" else "看空"),
                                "去 Unusual Whales 看这条警报的细节再决定"))
                        else:
                            alerts.append(_alert(
                                ticker, "uw_alert_direction_unknown",
                                f"实时警报触发但方向判断不了：{'、'.join(names)}",
                                "你配置的扫描规则触发了，但这批数据没法"
                                "确定是偏多还是偏空",
                                "去 Unusual Whales 看这条警报的细节，"
                                "自己判断方向"))
        except Exception as exc:
            gaps.append(f"UW 实时警报拉取失败：{exc}")

        alerts.sort(key=lambda a: (-a["severity"], a["ticker"]))
        alert_count = len(alerts)
        returned = alerts[:MAX_ALERTS]
        truncated = alert_count > len(returned)

        message = ""
        if not returned:
            message = f"{len(rows)} 只持仓，无异常"

        return json.dumps({
            "alerts": returned,
            "alert_count": alert_count,
            "returned_count": len(returned),
            "truncated": truncated,
            "checked": len(rows),
            "checked_sources": sources,
            "hours": hours,
            "warnings": warnings,
            "data_gaps": sorted(set(gaps)),
            "message": message,
        }, ensure_ascii=False, indent=2, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
