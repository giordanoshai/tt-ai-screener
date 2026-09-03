"""小时线买点工具。

做波段的人是这么用两个周期的：**日线定方向，小时线定点位**。
日线告诉你「这只票值不值得做」，小时线告诉你「今天这个价能不能进、
止损放哪、上面压着什么」。这个模块只干后半件事。

## 数据从哪来：先实时，拿不到才查库

两个市场都是「用到谁拉谁」——美股走 Unusual Whales、A股走新浪的 60 分钟线，
两边都取不到才退回下面这张表。返回体里的 **`source` 字段标着这一次走的哪条路**
（`uw_live` / `akshare_live` / `db`），理由和实测数据见 `_load_bars` 的注释。

## 这张表的三个坑（退回库里那条路上，每一条都能把查询搞砸）

1. **小时线跟分钟线挤在同一张表 `stock_ohlcv_min` 里**，靠 `timeframe` 列区分：
   小时线是 `'1h'`，分钟线是 `'1m'` / `'5m'`。2026-08 实测分钟线有 2000 万行、
   9.3 GB，漏掉 `timeframe = '1h'` 这个条件就会一头扎进去
   （实测执行计划代价从 6 涨到 5395 万）。
2. **必须带 `market` 条件**，表里混了美股（`US`）和 A股（`CN`）。
   工具签名里没有市场参数时，从代码后缀推：`.SH` / `.SZ` / `.BJ` 是 A股，其余是美股。
3. **占位符是 `%s` 不是 `?`**（psycopg2，不是 DuckDB）。

查表而不是查视图 `v_ohlcv_hourly`，理由见 `HOURLY_SQL` 上面的注释。

## 时间戳语义（算错这条，所有点位整体错一根）

**`ts` 是这根 K 线【开始】的时刻，不是结束时刻。**
美股来自 Unusual Whales 的 `start_time`，本来就是开始时刻；
A股新浪给的是结束时刻（只有 10:30/11:30/14:00/15:00 四种），
采集侧已经统一减 60 分钟对齐，所以 A股一天四根是
`09:30 / 10:30 / 13:00 / 14:00`（上午两根、午休断开、下午两根）。

`ts` 是带时区的（timestamptz），绝对时刻是准的，但直接读出来是 UTC——
盘口时间看着全是错位的。所以在 SQL 里就按市场换算成当地时间
（美股用纽约、A股用上海），换算交给 Postgres 做，不依赖机器上有没有 tzdata。
"""
import json
from datetime import datetime, timedelta, timezone

from core import akshare, uw
from db import pg

# 每个市场的当地时区。做波段的人脑子里的「09:30 开盘」是这个时区的 09:30。
MARKET_TZ = {"US": "America/New_York", "CN": "Asia/Shanghai"}

# A股代码后缀：上交所 / 深交所 / 北交所
_CN_SUFFIXES = (".SH", ".SZ", ".BJ")

# 一次最多、最少看多少根。上限是防着有人一把拉一年的小时线回来；
# 下限 20 是因为 20 小时均线要 20 根，再少这个工具就没什么可算的了。
MIN_BARS, MAX_BARS = 20, 200

# 摆动高低点的左右确认根数：一根 K 线的高点要同时高过左右各 2 根才算一个「顶」。
_SWING_WINDOW = 2

_RSI_PERIOD = 14
_ATR_PERIOD = 14
_VOL_WINDOW = 20      # 量能对比的基准窗口
_SLOPE_LOOKBACK = 5   # 均线斜率回看几根

# 直接查表，不查视图 v_ohlcv_hourly。两个原因：
#   1. 视图不暴露 `session` 和 `timeframe` 列。美股的 session 分 RTH（盘中）和
#      ETH（盘前盘后），做波段的人需要知道手里这批 K 线有没有混进盘后——
#      走视图就没法看，也没法过滤。
#   2. 视图是个简单视图，Postgres 会把它内联展开，执行计划跟直接查表一模一样
#      （实测两边都是 Index Scan using stock_ohlcv_min_ticker_tf_ts_idx，
#      代价都是 6.33），所以走视图并没有性能好处，只是把 `timeframe = '1h'`
#      这个关键条件藏了起来。写在自己的 SQL 里，读代码的人一眼能看见。
HOURLY_SQL = """
    select ts,
           ts at time zone %s as ts_local,
           session, open, high, low, close, volume, market
    from stock_ohlcv_min
    where ticker = %s
      and market = %s
      and timeframe = '1h'
    order by ts desc
    limit %s
"""


#: 每个市场的实时小时线接口。两个市场现在是同一个规矩：**先实时，
#: 拿不到才退回数据库**。两个模块的 `hourly()` 都承诺「取不到返回空列表、
#: 不抛异常」，都返回正序（最后一根最新）。
#:
#: 存的是**模块**不是函数：存函数的话这里在导入时就把 `uw.hourly` 绑死了，
#: 测试里 `patch("core.uw.hourly")` 会换不掉它——桩没生效，用例却照样绿。
_LIVE_SOURCE = {
    "CN": ("akshare_live", akshare),
    "US": ("uw_live", uw),
}


def _load_bars(ticker: str, market: str, tz: str,
               bars: int) -> tuple[list[dict], str]:
    """取小时线。**先走实时接口，拿不到才退回数据库。** 返回 (行, 数据来源)。

    两个市场都这么走，但走到这一步的理由不太一样：

      * A股（2026-09-01 改的）。库里的小时线只覆盖【活跃池】（约 150 只），
        而活跃池的粗筛条件是「站上 50 日均线 + 放量」——那是在挑今天强势的票，
        跟「用户想看哪只票」完全是两回事。当时实测贵州茅台、五粮液、宁德时代、
        比亚迪，四只最有名的 A股，库里一根小时线都没有。新浪一次调用给约
        1970 根、2~3 秒，而这里最多只要 200 根。

      * 美股（2026-09-03 改的）。`tt-data-hub` 把 `us_hourly` 从每天定时改成了
        按需跑，crontab 里 09:40 那行撤掉了——**库里的美股小时线从此不再自己
        更新**，会一天比一天旧。同一天顺带发现覆盖面也是缺的：库里只有活跃池
        那 247 只票有小时线，池子外的票一根都没有，跟 A股当时的毛病一模一样。
        UW 的 1h 接口一次给整个 3M 窗口（390 根盘中线）、约 13 秒。

    退回数据库这条路留着，是因为实时那头随时可能不通（没配密钥、代理挂了、
    超时），库里的旧数据总比什么都没有强——但**必须让调用方知道这是旧的**，
    所以来源要一路带出去，见 `hourly_setup` 里的 `source` 字段。

    返回顺序跟 `HOURLY_SQL` 一致（`order by ts desc`，最新的在前），
    调用方后面那句 `reversed` 两条路径通用。
    """
    source, live_module = _LIVE_SOURCE.get(market, (None, None))
    if live_module is not None:
        try:
            live = live_module.hourly(ticker, bars)
        except Exception:
            # core/akshare.py 和 core/uw.py 本来就承诺不抛异常，这层是兜底：
            # 实时那一路出任何问题都只该降级，不该让整个工具失败。
            live = []
        if live:
            # 两个实时接口给的都是正序（最后一根最新），翻成 SQL 的倒序
            return list(reversed(live)), source
    return pg.query(HOURLY_SQL, (tz, ticker, market, bars)), "db"


# --------------------------------------------------------------- 小工具


def _infer_market(ticker: str) -> str | None:
    """从代码推市场。推不出来返回 None（调用方会报错提示怎么写）。"""
    if ticker.endswith(_CN_SUFFIXES):
        return "CN"
    if ticker.isdigit():
        return None  # A股裸代码，分不清 .SH 还是 .SZ
    return "US"


def _fmt_ts(row) -> str:
    """把一行的当地时间格式化成 '2026-08-28 09:30'。"""
    value = row.get("ts_local") or row.get("ts")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def _num(value, default=0.0) -> float:
    return default if value is None else float(value)


def _round(value, digits=4):
    return None if value is None else round(value, digits)


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _rsi(closes: list[float], period: int = _RSI_PERIOD) -> float | None:
    """RSI，Wilder 平滑。白话：最近这段涨的力气和跌的力气谁大，0~100。

    70 以上叫超买（涨太急，追进去容易被套一下），30 以下叫超卖。
    """
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for prev, cur in zip(closes, closes[1:]):
        delta = cur - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_gain == 0 and avg_loss == 0:
        return 50.0  # 完全不动，给个中性值，不是 100
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _atr(highs, lows, closes, period: int = _ATR_PERIOD) -> float | None:
    """ATR，Wilder 平滑。白话：这只票平均每根小时线上下晃多少钱。

    止损放多远直接看它——止损放得比一根 K 线的正常晃动还近，
    就是在等着被随机波动扫掉。
    """
    if len(closes) < period + 1:
        return None
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        prev_close = closes[i - 1]
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - prev_close),
                       abs(lows[i] - prev_close)))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _swing_points(values: list[float], window: int, high: bool) -> list[float]:
    """找摆动高点（或低点）：比左右各 window 根都高（低）的那一根。

    这些位置是市场真正掉过头的地方，比区间的最高最低更有参考价值——
    价格冲到那里通常会先撞一下。
    """
    points = []
    for i in range(window, len(values) - window):
        neighbours = values[i - window:i] + values[i + 1:i + window + 1]
        if not neighbours:
            continue
        if high and values[i] > max(neighbours):
            points.append(values[i])
        elif not high and values[i] < min(neighbours):
            points.append(values[i])
    return points


# --------------------------------------------------------------- 主函数


def hourly_setup(ticker: str, bars: int = 60,
                 market: str | None = None) -> dict:
    """看一只票在小时线上的进出场位置：现价卡在哪、上面压着什么、下面托着什么。

    日线定方向，小时线定点位。日线看好了一只票之后，用这个工具回答
    「今天这个价位能不能进、止损放多远、离上方阻力还有多少空间」。
    只做波段和摆动交易用，不是日内工具。

    返回的是一份**结论摘要**（区间位置、均线、量能、波动、支撑阻力、
    一句话总结），不是原始 K 线数组。

    ticker: 股票代码。美股直接写 AAPL；A股要带交易所后缀，像 600519.SH、000001.SZ
    bars:   看最近多少根小时线，默认 60 根（美股约 9 个交易日，A股约 15 个）。
            会被限制在 20~200 之间
    market: 一般不用填，从代码后缀自动判断。只在自动判断不对时才手动指定 US / CN
    """
    try:
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return {"error": "没给股票代码"}

        market = (market or _infer_market(ticker) or "").upper()
        if market not in MARKET_TZ:
            return {
                "error": f"认不出 {ticker} 属于哪个市场。"
                         "A股要带交易所后缀（600519.SH / 000001.SZ / 430139.BJ），"
                         "美股直接写代码（AAPL）。"
                         "也可以用 market 参数手动指定 US 或 CN。",
            }

        bars = max(MIN_BARS, min(int(bars), MAX_BARS))
        tz = MARKET_TZ[market]

        rows, source = _load_bars(ticker, market, tz, bars)
        if not rows:
            return {
                "error": f"{ticker}（{market}）没有小时线数据。"
                         "实时接口和数据库都没取到，多半是这只票停牌、退市，"
                         + ("或者 AKTools 服务不通。" if market == "CN" else
                            "是指数代码（UW 的股票接口不收 ^ 开头的），"
                            "或者 UW 那边没配密钥 / 不通。"),
            }

        # SQL 是 order by ts desc，翻回正序，最后一根就是最新的
        rows = list(reversed(rows))
        closes = [_num(r["close"]) for r in rows]
        highs = [_num(r["high"]) for r in rows]
        lows = [_num(r["low"]) for r in rows]
        volumes = [_num(r["volume"]) for r in rows]
        n = len(rows)

        last_close = closes[-1]
        recent_high = max(highs)
        recent_low = min(lows)
        high_row = rows[highs.index(recent_high)]
        low_row = rows[lows.index(recent_low)]

        # 现价在这段区间的什么位置：0 = 贴着最低，100 = 贴着最高
        span = recent_high - recent_low
        position_pct = round((last_close - recent_low) / span * 100, 1) if span else 50.0

        # ---- 趋势 ----
        ma_20h = _sma(closes, 20)
        ma_50h = _sma(closes, 50)
        above_20 = None if ma_20h is None else last_close > ma_20h
        above_50 = None if ma_50h is None else last_close > ma_50h

        slope_pct = None
        if n >= 20 + _SLOPE_LOOKBACK:
            past_ma = _sma(closes[:-_SLOPE_LOOKBACK], 20)
            if past_ma:
                slope_pct = round((ma_20h - past_ma) / past_ma * 100, 3)

        rsi = _rsi(closes)

        # ---- 量能：最后一根跟之前 20 根的均量比 ----
        vol_ratio = None
        avg_volume = None
        if n >= 2:
            window = volumes[max(0, n - 1 - _VOL_WINDOW):n - 1]
            avg_volume = sum(window) / len(window) if window else 0.0
            if avg_volume > 0:
                vol_ratio = round(volumes[-1] / avg_volume, 2)

        # 盘中最后一根还没走完，量天然偏小，不标出来会被误读成缩量
        last_ts = rows[-1]["ts"]
        last_bar_complete = True
        if isinstance(last_ts, datetime) and last_ts.tzinfo is not None:
            last_bar_complete = (
                datetime.now(timezone.utc) - last_ts >= timedelta(hours=1))

        # ---- 波动，用来定止损 ----
        atr = _atr(highs, lows, closes)
        atr_pct = round(atr / last_close * 100, 2) if atr and last_close else None

        # ---- 关键位：上方最近的顶、下方最近的底 ----
        highs_above = [h for h in _swing_points(highs, _SWING_WINDOW, True)
                       if h > last_close]
        lows_below = [l for l in _swing_points(lows, _SWING_WINDOW, False)
                      if l < last_close]
        resistance = min(highs_above) if highs_above else recent_high
        support = max(lows_below) if lows_below else recent_low

        sessions = sorted({r.get("session") for r in rows if r.get("session")})

        result = {
            "ticker": ticker,
            "market": market,
            "timeframe": "1h",
            "bars_used": n,
            "sessions": sessions,
            "last_bar_complete": last_bar_complete,
            # 这批 K 线是实时拉的还是从库里翻出来的。**必须交出去**：
            # 库里的美股小时线自 2026-09-03 起不再每天更新（上游改成了按需），
            # 退回库里拿到的可能是好几天前的行情，而拿旧行情算出来的支撑阻力
            # 看着跟新的一模一样，不标出来没人分得清
            "source": source,
            "period": {
                "from": _fmt_ts(rows[0]),
                "to": _fmt_ts(rows[-1]),
                "timezone": tz,
                "note": "时间是每根 K 线【开始】的时刻，不是结束时刻",
            },

            # 现价卡在哪
            "last_close": _round(last_close),
            "recent_high": _round(recent_high),
            "recent_high_at": _fmt_ts(high_row),
            "recent_low": _round(recent_low),
            "recent_low_at": _fmt_ts(low_row),
            "position_pct": position_pct,
            "dist_to_high_pct": round((recent_high - last_close) / last_close * 100, 2)
                                if last_close else None,
            "dist_to_low_pct": round((last_close - recent_low) / last_close * 100, 2)
                               if last_close else None,

            # 方向
            "ma_20h": _round(ma_20h),
            "ma_50h": _round(ma_50h),
            "above_ma_20h": above_20,
            "above_ma_50h": above_50,
            "ma_20h_slope_pct": slope_pct,
            "rsi_14h": _round(rsi, 2),

            # 量能
            "last_volume": _round(volumes[-1], 0),
            "avg_volume_20h": _round(avg_volume, 0),
            "vol_ratio": vol_ratio,

            # 波动，止损放多远看这个
            "atr_14h": _round(atr),
            "atr_pct": atr_pct,

            # 上下最近的关键位
            "resistance": _round(resistance),
            "support": _round(support),
            "dist_to_resistance_pct": round((resistance - last_close) / last_close * 100, 2)
                                      if last_close else None,
            "dist_to_support_pct": round((last_close - support) / last_close * 100, 2)
                                   if last_close else None,
        }
        result["signals"] = _signals(result)
        result["summary"] = _summary(result)
        return result

    except Exception as exc:
        return {"error": str(exc)}


def get_hourly_setup(ticker: str, bars: int = 60,
                     market: str | None = None) -> str:
    """（保留给现有测试和网页面板用；MCP 那边已经并进 analyze_ticker）"""
    return json.dumps(hourly_setup(ticker, bars, market),
                      ensure_ascii=False, default=str)


# --------------------------------------------------------------- 结论


def _signals(r: dict) -> list[str]:
    """把上面那堆数字翻成人话。做波段的人扫一眼这几条就知道该不该动手。"""
    out = []

    for period, above, ma in (("20", r["above_ma_20h"], r["ma_20h"]),
                              ("50", r["above_ma_50h"], r["ma_50h"])):
        if ma is None:
            continue
        if above:
            out.append(f"站上 {period} 小时均线（{ma}）")
        elif r["last_close"] < ma:
            out.append(f"跌破 {period} 小时均线（{ma}）")
        else:
            out.append(f"正好贴在 {period} 小时均线上（{ma}）")

    slope = r["ma_20h_slope_pct"]
    if slope is not None:
        if slope > 0.2:
            out.append("20 小时均线在往上走")
        elif slope < -0.2:
            out.append("20 小时均线在往下走")
        else:
            out.append("20 小时均线走平")

    ratio = r["vol_ratio"]
    if ratio is not None:
        if ratio >= 1.5:
            out.append(f"放量 {ratio} 倍（对比前 20 根均量）")
        elif ratio <= 0.6:
            out.append(f"缩量，只有均量的 {ratio} 倍")
        else:
            out.append(f"量能正常（{ratio} 倍均量）")

    pos = r["position_pct"]
    if pos >= 85:
        out.append(f"贴在区间上沿（{pos}%），追高要小心")
    elif pos >= 60:
        out.append(f"在区间中上部（{pos}%）")
    elif pos > 40:
        out.append(f"在区间中部（{pos}%）")
    elif pos > 15:
        out.append(f"在区间中下部（{pos}%）")
    else:
        out.append(f"贴在区间下沿（{pos}%）")

    rsi = r["rsi_14h"]
    if rsi is not None:
        if rsi >= 70:
            out.append(f"小时线 RSI {rsi}，超买（涨得太急）")
        elif rsi <= 30:
            out.append(f"小时线 RSI {rsi}，超卖（跌得太狠）")

    if r.get("source") == "db":
        # 上游 2026-09-03 把美股小时线改成按需采集之后，库里那份不再每天
        # 更新。退回库里时如果不说一声，一批三天前的 K 线算出来的支撑阻力
        # 看着跟实时的一模一样——这条提示就是把这件事写在脸上。
        out.append(f"这批小时线是从库里翻出来的（实时接口没取到），"
                   f"最后一根停在 {r['period']['to']}，可能不是最新行情")

    if not r["last_bar_complete"]:
        out.append("最后一根小时线还没走完，量和收盘价都还会变")
    if "ETH" in (r["sessions"] or []):
        out.append("这批 K 线混了盘前盘后（ETH），那几根的量不能跟盘中比")

    return out


def _summary(r: dict) -> str:
    """一句话总结，给「现在能不能进」一个直接的落点。"""
    parts = [f"{r['ticker']} 现价 {r['last_close']}，"
             f"在最近 {r['bars_used']} 根小时线区间"
             f"（{r['recent_low']}~{r['recent_high']}）的 {r['position_pct']}% 位置"]

    if r["support"] is not None and r["resistance"] is not None:
        parts.append(f"下方支撑 {r['support']}（-{r['dist_to_support_pct']}%）、"
                     f"上方阻力 {r['resistance']}（+{r['dist_to_resistance_pct']}%）")
    if r["atr_14h"]:
        parts.append(f"每根小时线平均晃 {r['atr_14h']}（{r['atr_pct']}%），"
                     "止损别放得比这更近")
    if r["vol_ratio"] is not None:
        parts.append(f"量能 {r['vol_ratio']} 倍")

    return "；".join(parts) + "。"
