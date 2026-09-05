# -*- coding: utf-8 -*-
"""Unusual Whales 实时接口客户端。

## 为什么有些数据走实时、有些走数据库

库里的期权数据一天只灌两次（美东 9:30 和 16:00），拿它「盯盘」名不副实。
所以持仓盯盘和个股分析走实时，全市场扫描走数据库——2000 多只票的均线、RSI、
ATR 库里已经算好了，查一次 0.1 秒；换成实时要调 2000 多次日线接口，
并发 20 路也要 15 分钟，在对话里等不到。

## 「中转」和「代理」是两个东西，别搞混

底下会反复出现两个容易混为一谈的词，先说清楚：

* **中转**：`.env` 里那个 `https://ai-tool.indevs.in/unusualwhales`，
  是 UW 官方接口的转发站，不直连官方域名。它挡在 Cloudflare 后面。
* **代理**：`UW_PROXY`，本机的翻墙代理（开发机 1080，服务器 1081），
  用来把请求送出国。见下面 `get()` 的注释。

下面第 1 条说的是中转站的脾气，跟代理没关系。

## 两个必须知道的坑

1. **必须带浏览器 User-Agent。** 中转站挡在 Cloudflare 后面，Python 默认的
   `Python-urllib/3.12` 会被当成爬虫，直接 403（页面里写的是 Error 1010）。
   这跟 token 没关系，换任何鉴权写法都救不了。
2. **`/api/alerts` 返回的 `symbol` 是期权合约代码**（`NVDA261002P00190000`），
   不是股票代码。标的在 `meta.underlying_symbol` 里。实测 100 条里还有 14 条
   `potus_schedule`，`symbol` 直接是 null。想按持仓过滤必须先解出标的。

## 失败就是 None

这个模块的任何函数**都不抛异常**。取不到就返回 None，调用方据此走降级并在
`data_gaps` 里写明这一路是缺的。一个外部接口慢，不该让整个工具失败。

## 小时线为什么也搬到这里来了（2026-09-03）

`tt-data-hub` 那边把 `us_hourly` 从每天定时改成了按需跑（`--tickers AAPL,TSLA`），
crontab 里 09:40 那行撤掉了。理由是 UW 的 1h 接口一次就给整个 3M 窗口，
随时拉随时有完整历史，不靠每天跑来攒——那边每天写 8 万行里 98% 是拿同样的
旧数据覆盖自己。

对本仓库的后果是：**库里的美股小时线从此不再自己更新**，会一天比一天旧。
所以这边改成跟 A股同一个规矩——用到谁拉谁，库里那批留着当兜底。
顺带补上了另一个老缺口：库里只有活跃池那 247 只票有小时线，池子外的票
（用户随口问的任何一只）本来就一根都没有。
"""
import concurrent.futures
import datetime
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request

import config
from core import net

try:
    from zoneinfo import ZoneInfo
    _NY_TZ = ZoneInfo("America/New_York")
except Exception:      # 机器上没装 tzdata。ts_local 留空，不影响别的字段
    _NY_TZ = None

#: 不带这个头会被 Cloudflare 403。见模块注释。
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")

#: 并发取多只票时的线程数。6 只持仓一轮 4 秒左右，再高对中转站不礼貌。
MAX_WORKERS = 6

#: 期权合约代码（OCC 格式）里，股票代码之后就是 6 位到期日。
#: NVDA261002P00190000 -> NVDA
_OCC_TAIL_LEN = 15

#: K 线接口的窗口。UW 的 timeframe 是档位制（1M/3M/6M/YTD/1Y/2Y/3Y/5Y），
#: 没有「刚好 N 根」这一档。
#:
#: 取 3M 的两个依据，都是 2026-09-03 拿 AAPL 实测的：
#:   * 1M 档只有 **138 根**盘中线，撑不起 `tools/hourly.py` 的 MAX_BARS=200；
#:     3M 档有 390 根，留足了假期和停牌缺根的余量。
#:   * 换小窗口省不到时间——1M 档 12.9 秒、3M 档 14.0 秒，耗时几乎全是
#:     中转站那边的固定开销，跟传多少行没什么关系。所以也别为了「快一点」
#:     改回 1M，只会换来一条永远算不出来的 200 小时均线。
#: 同样别往上加档：更大的窗口纯粹多传数据，这里最多只用 200 根。
#: `tt-data-hub` 的 `hub/jobs/us_hourly.py` 用的是同一个档位。
OHLC_WINDOW = "3M"

#: K 线响应里 `market_time` 的三种取值：r=盘中、pr=盘前、po=盘后。
#: 只要盘中那一档——`tt-data-hub` 写进库里的也只有这一档（那边 session
#: 一律记成 'RTH'），两条路口径必须一致，不然实时和库里给出来的
#: 「最近 60 根」根本不是同一段行情。
REGULAR_SESSION = "r"

#: 拉 K 线的超时。实测单次 12.9~14.0 秒，贴着默认的 20 秒——一次网络抖动
#: 就整条失败退回库里，而库里可能根本没有这只票。所以单独给足余量。
OHLC_TIMEOUT = max(config.UW_TIMEOUT, 30.0)


def available() -> bool:
    """有没有配密钥。没配就别调，直接走库里的数据。"""
    return bool(config.UW_API_TOKEN)


def _to_float(value, default: float = 0.0) -> float:
    """UW 把数值全用字符串返回，统一转一下。转不动就给默认值。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


#: 已经喊过的话，喊过就不再喊。一轮盯盘要调几十次接口，同一个配错
#: 刷几十行的话，日志就没人看了，等于又回到了静默失败。
_WARNED: set[str] = set()


def reset_warnings() -> None:
    """清空「喊过了」的记录。给测试用，也给长跑进程留个手动复位的口子。"""
    _WARNED.clear()


def _warn_once(key: str, message: str) -> None:
    """同一件事只往 stderr 喊一次。

    走 stderr 不是随便选的：MCP 的 stdio 模式下 stdout 是协议通道，
    往那儿写一个字都会把连接搞坏。`core/notify.py` 也是这个规矩。
    """
    if key in _WARNED:
        return
    _WARNED.add(key)
    print(message, file=sys.stderr)


def _fetch(request: urllib.request.Request, timeout: float, proxy: str):
    """真正发请求的那一层。异常照抛，由 `get()` 统一吞掉。

    单独抽出来只为一件事：测试里能整体换掉。跑测试的机器上配着真密钥，
    不拦的话每个用例都会静悄悄地发真实请求，结果随线上行情变化——
    同一份代码今天绿明天红。见 `tests/conftest.py`。

    `proxy` 为空串时跟随系统的 `HTTP_PROXY`，也就是这一路一直以来的
    行为，见 `get()` 里那段注释。
    """
    opener = net.build_opener(proxy, env_when_empty=True)
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read())


def get(path: str, params: dict | None = None,
        timeout: float | None = None) -> list[dict] | None:
    """调一个接口，返回 data 数组。任何失败都返回 None，不抛。

    ## 出网走哪条路

    UW 要出国。2026-09-05 之前这一路**没有任何代理配置**——用的是
    `urllib.request.urlopen`，而它自己会去读 `HTTP_PROXY` 环境变量，
    开发机上那个变量一直设着，所以「碰巧」一直是走代理的。

    碰巧不算数：MCP 由 Claude Desktop 以子进程拉起、或者服务器上用
    systemd / 计划任务跑的时候，那些变量不一定继承得到。一旦掉了，
    这里每个接口都返回 None，而调用方只会表现成「期权数据怎么全是空的」
    「美股小时线怎么又退回库里了」——库里那份从 2026-09-03 起就不再更新，
    等于拿着越来越旧的数据当实时用，还看不出根因。

    所以给了显式的 `UW_PROXY`：配了就走它，留空才跟随环境变量。
    """
    if not available():
        return None

    proxy = net.normalize_proxy(config.UW_PROXY)
    if proxy is None:
        # 配了 SOCKS。不能假装直连——那会超时十几秒再返回 None，
        # 看日志完全不知道该改什么。跟 `core/notify.py` 同一个处理。
        _warn_once("proxy:socks",
                   "UW 取不到数据：UW_PROXY 配的是 SOCKS 代理（%s），"
                   "而 Python 标准库的 urllib 只支持 HTTP/HTTPS 代理。"
                   "请改配代理软件的 HTTP 端口（多数软件两个端口同时开着）。"
                   % config.UW_PROXY)
        return None

    url = config.UW_API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {config.UW_API_TOKEN}",
        "Accept": "application/json",
        "User-Agent": BROWSER_UA,   # 少了这行就是 403，见模块注释
    })
    try:
        payload = _fetch(request, timeout or config.UW_TIMEOUT, proxy)
    except Exception as exc:
        # 返回值仍然是 None——调用方的降级契约一个字都没变。但要留下痕迹：
        # 「代理断了」和「这只票本来就没有期权数据」在 None 面前长得
        # 一模一样，不说一声就永远分不出来。按异常类型去重，一类只喊一次。
        _warn_once("fetch:%s" % type(exc).__name__,
                   "UW 请求失败（%s）：%r。后续同类失败不再重复提示。"
                   % ("经代理 " + proxy if proxy else "跟随系统代理设置", exc))
        return None

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        return data if isinstance(data, list) else None
    return None


def get_many(paths: list[str],
             timeout: float | None = None) -> dict[str, list[dict] | None]:
    """并发调一批接口。键是原样的 path，取不到的那条值是 None。"""
    if not paths:
        return {}
    out: dict[str, list[dict] | None] = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(MAX_WORKERS, len(paths))) as pool:
        futures = {pool.submit(get, p, None, timeout): p for p in paths}
        for future in concurrent.futures.as_completed(futures):
            out[futures[future]] = future.result()
    return out


def flow_alerts(ticker: str, limit: int = 50) -> list[dict] | None:
    """一只票近期的期权异动。实测 3.6 秒。"""
    return get(f"/api/stock/{ticker}/flow-alerts", {"limit": limit})


def flow_alerts_many(tickers: list[str],
                     limit: int = 50) -> dict[str, list[dict] | None]:
    """一批票的期权异动，并发取。键是股票代码。"""
    paths = {f"/api/stock/{t}/flow-alerts?limit={limit}": t for t in tickers}
    raw = get_many(list(paths))
    return {paths[path]: rows for path, rows in raw.items()}


# --------------------------------------------------------------- 小时线


def _bar_price(value):
    """转成正的有限浮点数，转不成返回 None。

    UW 把价格全用字符串返回（`"324.74"`），所以不能直接拿来算。
    必须挡住 NaN：`float('nan')` 既不是假值、也不满足 `<= 0`，
    `if not x or x <= 0` 这种写法会放它过去，一路算进均线。
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) and num > 0 else None


def _bar_volume(value) -> float:
    """成交量。取不到当 0——量能是加分项，缺了不该把整根 K 线丢掉。"""
    volume = _to_float(value)
    return volume if math.isfinite(volume) else 0.0


def parse_ohlc_1h(payload) -> list[dict]:
    """把 UW 的 1h 响应转成**跟库里那条 SQL 一样形状**的行。纯函数。

    形状要对齐 `tools/hourly.py::HOURLY_SQL`：ts / ts_local / session /
    open / high / low / close / volume / market。对不齐的话调用方就得为
    「实时」和「库里」写两套解析。跟 `core/akshare.py::parse_minute_60`
    是同一个约定。

    三件保命的事：
      1. **只留盘中那一档。** 2026-09-03 实测 AAPL 的 3M 档 1040 行里，
         盘中 390 行、盘前 390 行、盘后 260 行——盘前盘后照单全收的话，
         「最近 60 根」有一半是夜里那几根几千手的稀量线，均线、量比、
         支撑阻力全被带偏。库里也只存盘中这一档。
      2. 四个价格一起校验，缺一个就整根丢掉——不能拿 None 去算均线。
      3. 按时间戳去重，重复的留最后一条。

    `ts` 直接用响应里的 `start_time`，它本来就是这根 K 线【开始】的时刻
    （A股那边新浪给的是结束时刻，要减一根，这里不用）。错一根的话
    所有点位整体错一格。
    """
    by_ts: dict[datetime.datetime, dict] = {}

    for raw in payload or []:
        if not isinstance(raw, dict) or raw.get("market_time") != REGULAR_SESSION:
            continue
        prices = {name: _bar_price(raw.get(name))
                  for name in ("open", "high", "low", "close")}
        if any(value is None for value in prices.values()):
            continue
        try:
            ts = datetime.datetime.fromisoformat(
                str(raw.get("start_time", "")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)

        by_ts[ts] = {
            "ts": ts,
            # 库里那列是 `ts at time zone 'America/New_York'`，出来不带时区。
            # 机器上没装 tzdata 时给 None，`tools/hourly.py::_fmt_ts`
            # 会退回显示 UTC 的 ts——差几个小时，但不至于整个工具挂掉
            "ts_local": (ts.astimezone(_NY_TZ).replace(tzinfo=None)
                         if _NY_TZ else None),
            "session": "RTH",     # 只留了盘中，采集侧写进库里的也是 RTH
            "market": "US",
            # total_volume 那个字段的数值是错的（tt-data-hub 早就踩过），
            # 只用 volume
            "volume": _bar_volume(raw.get("volume")),
            **prices,
        }

    return sorted(by_ts.values(), key=lambda row: row["ts"])


def hourly(ticker: str, bars: int = 60) -> list[dict]:
    """一只美股最近 `bars` 根小时线，正序（最后一根最新）。

    取不到就返回空列表，**不抛异常**——调用方据此退回数据库。
    """
    if not available():
        return []
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return []
    if ticker.startswith("^"):
        # 指数代码喂给 UW 的【股票】接口一律 422（^VIX 拉小时线、拉伽马墙
        # 都是这个下场）。tt-data-hub 2026-09-03 才因为自选里的 ^VIX 每天
        # 各报一条永远不会自愈的警报，在源头把它挡出了活跃池。这里同样在
        # 发请求之前挡掉：明知要 422 还去等十几秒，只是白白拖慢工具。
        return []

    rows = get(f"/api/stock/{ticker}/ohlc/1h", {"timeframe": OHLC_WINDOW},
               timeout=OHLC_TIMEOUT)
    if not rows:
        return []
    return parse_ohlc_1h(rows)[-max(int(bars), 1):]


def alerts(limit: int = 100) -> list[dict] | None:
    """用户自己配的实时警报流。实测 12~14 秒，是最慢的一个接口。"""
    return get("/api/alerts", {"limit": limit})


def news_headlines(limit: int = 50) -> list[dict] | None:
    """最近的新闻标题。只有 is_major 布尔值，没有 High/Medium/Low 分级。"""
    return get("/api/news/headlines", {"limit": limit})


def underlying_of(alert: dict) -> str | None:
    """从一条警报里解出股票代码。解不出返回 None。

    优先用 meta.underlying_symbol；没有就从期权合约代码前缀切
    （OCC 格式：代码 + 6 位到期日 + C/P + 8 位行权价 = 后缀恰好 15 位）。
    """
    meta = alert.get("meta") or {}
    underlying = meta.get("underlying_symbol")
    if underlying:
        return str(underlying).upper()

    symbol = alert.get("symbol")
    if not symbol:
        return None
    symbol = str(symbol).upper()
    if len(symbol) > _OCC_TAIL_LEN and symbol[-_OCC_TAIL_LEN:].isalnum():
        head = symbol[:-_OCC_TAIL_LEN]
        if head.isalpha():
            return head
    return symbol if symbol.isalpha() else None


def split_premium(kind: str | None, ask_prem: float,
                  bid_prem: float) -> tuple[float, float]:
    """把一条期权异动的溢价拆成「看多多少、看空多少」。

    白话：在卖价成交说明是买方主动（急着买），在买价成交说明是卖方主动。
    买看涨 = 看多，卖看涨 = 看空；买看跌 = 看空，卖看跌 = 看多。
    """
    if kind == "call":
        return ask_prem, bid_prem
    if kind == "put":
        return bid_prem, ask_prem
    return 0.0, 0.0


def summarize_flow(rows: list[dict] | None) -> dict:
    """把一只票的一堆期权异动汇总成四个数。rows 为空也返回结构完整的零值。"""
    bullish = bearish = biggest = 0.0
    count = 0
    for row in rows or []:
        ask = _to_float(row.get("total_ask_side_prem"))
        bid = _to_float(row.get("total_bid_side_prem"))
        up, down = split_premium(row.get("type"), ask, bid)
        bullish += up
        bearish += down
        biggest = max(biggest, _to_float(row.get("total_premium")))
        count += 1
    return {
        "bullish_premium": round(bullish, 2),
        "bearish_premium": round(bearish, 2),
        "net_premium": round(bullish - bearish, 2),
        "biggest_premium": round(biggest, 2),
        "alert_count": count,
        "source": "uw_live",
    }
