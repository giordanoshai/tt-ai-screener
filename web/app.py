# -*- coding: utf-8 -*-
"""网页层。数据全部来自自建 Postgres，而且**只读**。

## 这一版改了什么

原来这个文件连的是本地 DuckDB 文件，而且跟 MCP 工具各写各的 SQL —— 同一件事
（列持仓、加自选、筛股）在两个地方各有一份实现，行为还不一样。这一版把重复的
那部分全删了，端点直接调 ``tools/`` 里已经写好、测过的函数：

| 端点 | 现在调谁 |
|---|---|
| ``GET  /api/positions``        | ``tools.positions.list_positions`` |
| ``POST /api/positions/add``    | ``tools.positions.add_position`` |
| ``POST /api/positions/remove`` | ``tools.positions.remove_position`` |
| ``POST /ticker/add``           | ``tools.watchlist.add_to_watchlist`` |
| ``POST /ticker/remove``        | ``tools.watchlist.remove_from_watchlist`` |
| ``GET  /watchlist``            | ``tools.watchlist.list_watchlist`` |

只有两个端点还自己写查询，因为 ``tools/`` 里确实没有对应的能力：

* ``POST /screen`` —— 筛选页支持的条件是 MCP 工具 ``screen_stocks`` 的**超集**
  （同时站上 50 和 200 日线、多头排列、离 20 日线的距离区间、放量倍数、逼近 20
  日新高、振幅下限、涨跌幅区间、是否在自选池里、最多 100 条）。硬套那个工具会
  把这些条件全丢掉，所以这里自己拼 SQL，但**最新交易日和行业别名两块直接复用
  ``tools.screening`` 里的函数**，不再抄第二份。
* ``GET /ticker/{ticker}`` —— 详情面板要的是「基本面 + 新闻 + 公司信息」三合一，
  没有对应工具。

## 采集功能整个搬走了

往库里写行情、基本面、新闻的活现在全在 **tt-data-hub**（另一个仓库，跑在服务器
上）。所以「立刻更新」「更新进度」这些端点在这里已经没有意义，统一返回 410 并
说清楚去哪儿看。表结构也由那边的建表脚本管，网页服务不建表。

**「定时更新时间」那组接口（``/settings/schedule``）连同设置页上的开关一起删掉了**，
不是改成 410。理由：它原来靠 ``main.py`` 里的定时线程干活，那套东西已经随采集一起
删了，接口留着也只是个点一下就报错的按钮。定时任务现在归 tt-data-hub 的 crontab。

## 写库只有两处

自选和持仓的增删改。其余一律走只读账号（``db.pg`` 里的读连接还额外设成只读
事务，是防误写的第二道闸）。

## 三个跟 DuckDB 时代不一样的地方

1. 占位符是 ``%s``，写成 ``?`` 会报 ``syntax error at or near "?"``。
2. 每一条查日线表的语句都必须带 ``market``：那张表混了 275 万行美股和 105 万行
   A股，漏了会把 A股串进美股结果里。
3. 取最新交易日走两段式（先单查那一天，再当常量传进主查询）。真库实测：相关子
   查询 6750 ms，两段式 109 ms。这一段直接用 ``tools.screening.latest_trading_day``。
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from db import pg
from tools import positions as positions_tool
from tools import watchlist as watchlist_tool
# 最新交易日和行业别名两件事在选股工具里已经写好并测过，这里复用而不是再抄一遍。
# _sector_candidates 带下划线是那边的内部函数，导入时换个不带下划线的名字，
# 表示「这是有意借用的」，同时提醒改那边的人这里也在用。
from tools.screening import _sector_candidates as sector_candidates
from tools.screening import latest_trading_day

app = FastAPI(title="TT AI Screener")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# 这个项目认的两个市场。日线表里就这两种 market 值。
MARKETS = ("US", "CN")

# 筛选页一次最多返回多少条。前端下拉最大给到 100，这里再留一倍余量兜底，
# 防止有人直接拿 curl 打一个 limit=100000 把页面撑死。
MAX_SCREEN_LIMIT = 200

DATA_HUB = "tt-data-hub"


# ── 通用小工具 ────────────────────────────────────────────────────────────────

def _tool_json(raw: str) -> dict:
    """把 tools/ 函数返回的 JSON 字符串解析成 dict。

    那些函数出错时返回的是 ``{"error": "..."}``，解析不了的情况理论上不该发生，
    真发生了也当成一个错误往上报，不要让页面看见一个 500。
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {"error": f"工具返回了不是 JSON 的东西：{raw!r}"}
    return data if isinstance(data, dict) else {"data": data}


def _tool_list(raw: str) -> tuple[list, str | None]:
    """同上，但用在返回列表的工具上。返回 (列表, 错误信息)。"""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return [], f"工具返回了不是 JSON 的东西：{raw!r}"
    if isinstance(data, dict):
        return [], data.get("error") or "工具返回了意料之外的结构"
    return data, None


def _moved_to_hub(feature: str, where: str) -> JSONResponse:
    """采集相关的端点统一这么回：说清楚搬到哪儿了、现在去哪看。"""
    return JSONResponse({
        "ok": False,
        "error": f"{feature}已经不在这个项目里了，采集全部由 {DATA_HUB} 负责。{where}",
    }, status_code=410)


def _as_datetime(value):
    """库里给的是 datetime，测试里给的是 ISO 字符串，两种都得认。"""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# ── 首页/状态：这些数只读，坏了也不能让页面打不开 ──────────────────────────────

def _market_status(market: str) -> dict:
    """某个市场的行情覆盖情况：数据截止到哪天、那天有多少只票。

    两条查询都带 market，都走 (market, date desc) 索引，各 35 毫秒。
    别顺手加一句 ``count(*) where market = ...`` —— 那是整段扫这个市场的全部行，
    实测一个市场就要 425 毫秒，两个市场把首页拖慢将近一秒，而且这个数没人看。
    总行数改用下面那个规划器估算值。
    """
    day = latest_trading_day(market)
    if day is None:
        return {"latest": None, "tickers": 0}
    on_day = pg.query_one(
        "select count(*) as c from stock_ohlcv_daily "
        "where market = %s and date = %s", (market, day))
    return {
        "latest": day.isoformat() if hasattr(day, "isoformat") else str(day),
        "tickers": (on_day or {}).get("c", 0),
    }


def _ohlcv_row_estimate() -> int:
    """日线表大概多少行。取 Postgres 自己维护的统计值，不真数。

    真数一遍 380 万行要 300 毫秒以上，而这个数字只是首页上一个「库有多大」的
    参考值，差个千分之四完全无所谓（实测估算 3819130 / 实际 3802494）。
    """
    row = pg.query_one("select reltuples::bigint as c from pg_class "
                       "where oid = 'stock_ohlcv_daily'::regclass")
    return max((row or {}).get("c") or 0, 0)


def _get_db_status() -> dict:
    """首页顶上那一排数字。

    分市场统计，不把美股和 A股混成一个数。老模板还在读几个扁平字段
    （ohlcv_tickers / ohlcv_latest / tickers / news_latest），所以这些键继续保留。
    """
    markets = {}
    for market in MARKETS:
        try:
            markets[market] = _market_status(market)
        except Exception as exc:
            markets[market] = {"latest": None, "tickers": 0, "error": str(exc)}

    try:
        ohlcv_rows = _ohlcv_row_estimate()
    except Exception:
        ohlcv_rows = 0
    try:
        news = pg.query_one(
            "select count(*) as c, max(published_at)::date as latest from news")
    except Exception:
        news = None
    try:
        watch = pg.query_one("select count(*) as c from user_watchlist")
    except Exception:
        watch = None

    latest_days = [m["latest"] for m in markets.values() if m.get("latest")]
    news_latest = (news or {}).get("latest")

    return {
        "markets": markets,
        "tickers": (watch or {}).get("c", 0),
        "ohlcv_rows": ohlcv_rows,  # 估算值，见 _ohlcv_row_estimate
        "ohlcv_tickers": sum(m.get("tickers", 0) for m in markets.values()),
        "ohlcv_latest": max(latest_days) if latest_days else "—",
        "news_count": (news or {}).get("c", 0),
        "news_latest": str(news_latest) if news_latest else "—",
    }


# tt-data-hub 每跑一个采集任务就往 ingest_runlog 写一行。原来那张 update_history
# 是本项目自己跑采集时写的，现在库里压根没有这张表，改看 hub 的运行日志。
_RUNLOG_SQL = """
    select job, market, status, started_at, finished_at,
           rows_written, tickers_total, tickers_failed, error
    from ingest_runlog
    order by id desc
    limit 1
"""


def _get_last_update() -> dict | None:
    """tt-data-hub 最近一次采集跑成什么样。查不到就返回 None。"""
    try:
        rows = pg.query(_RUNLOG_SQL)
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]

    started, finished = _as_datetime(row.get("started_at")), _as_datetime(row.get("finished_at"))
    duration = int((finished - started).total_seconds()) if started and finished else None

    total = row.get("tickers_total") or 0
    failed = row.get("tickers_failed") or 0
    # 首页模板按 {阶段: {fetched, skipped, errors, duration}} 这个形状渲染卡片，
    # hub 的日志是一任务一行，映射成一张「行情」卡片就够了。
    summary = {"ohlcv": {
        "fetched": max(total - failed, 0),
        "skipped": 0,
        "errors": failed,
        "duration": f"{duration}s" if duration is not None else "—",
    }} if total else {}

    return {
        "job": row.get("job"),
        "market": row.get("market"),
        "status": row.get("status"),
        "started_at": str(started) if started else None,
        "finished_at": finished.isoformat(sep=" ") if finished else None,
        "duration_s": duration,
        "rows_written": row.get("rows_written"),
        "error": row.get("error"),
        "summary": summary,
        "source": f"{DATA_HUB} / ingest_runlog",
    }


def _get_ai_models() -> list[dict]:
    """AI 模型清单。ai_models 这张表线上没有，取不到就当没配，不要连累首页。"""
    try:
        from ai_client import list_models
        return list_models() or []
    except Exception:
        return []


# ── 自选池 ────────────────────────────────────────────────────────────────────

def _get_watchlist() -> list[dict]:
    """自选池行情快照。SQL 在 tools/watchlist.py 里，这里只负责排版。"""
    rows, err = _tool_list(watchlist_tool.list_watchlist())
    if err:
        return []
    # 页面按板块分组看，工具那边是按加入时间排的，这里重排一次
    rows.sort(key=lambda r: ((r.get("sector") or "￿"), r.get("ticker") or ""))
    return rows


def _get_sectors(watchlist: list[dict]) -> list[str]:
    """自选池里出现过的板块。直接从上面那份数据里取，不再多打一次库。"""
    return sorted({(r.get("sector") or "").strip()
                   for r in watchlist if (r.get("sector") or "").strip()})


# 筛选页左边的板块下拉框。stocks_meta.sector 目前只有 1555 只票有值，
# 而主选股池 ticker_over20b 那 2164 只里有 2099 只带行业分类，所以两边取长补短。
# 这个兜底等 tt-data-hub 的 meta_refresh 把 stocks_meta.sector 填满就能撤掉。
_ALL_SECTORS_SQL = """
    with sector_src as (
        -- 主键是 (id, ticker)，不去重一只票会连出多行
        select distinct on (ticker) ticker, sector
        from ticker_over20b
        order by ticker, id desc
    )
    select coalesce(m.sector, ts.sector) as sector, count(*) as cnt
    from stocks_meta m
    left join sector_src ts on ts.ticker = m.ticker
    where coalesce(m.market, 'US') = %(market)s
      and coalesce(m.sector, ts.sector) is not null
      and coalesce(m.sector, ts.sector) <> ''
      -- 库里有一批票的 sector 字面就是 'NONE'，那是占位不是行业，别放进下拉框
      and upper(coalesce(m.sector, ts.sector)) not in ('NONE', 'N/A', 'UNKNOWN')
    group by 1
    order by cnt desc
"""


def _get_all_sectors(market: str = "US") -> list[str]:
    """筛选页的板块下拉。

    两个数据源的叫法不一样（「Health Care」和「Healthcare」、「Consumer
    Discretionary」和「Consumer Cyclical」），下拉里同时出现两种写法会让人以为是
    两个板块。所以按选股工具那套别名规则合并，每组只留最常见的那个写法当标签。
    """
    try:
        rows = pg.query(_ALL_SECTORS_SQL, {"market": market})
    except Exception:
        return []

    merged: dict[tuple, tuple[int, str]] = {}
    for row in rows:
        name = (row["sector"] or "").strip()
        if not name:
            continue
        key = tuple(sector_candidates(name))
        count = row["cnt"] or 0
        best = merged.get(key)
        if best is None or count > best[0]:
            merged[key] = (count + (best[0] if best else 0), name)
        else:
            merged[key] = (best[0] + count, best[1])
    return [label for _, (_, label) in
            sorted(merged.items(), key=lambda kv: -kv[1][0])]


# ── 请求体 ────────────────────────────────────────────────────────────────────

class ScreenRequest(BaseModel):
    sectors: list[str] = []
    above_ma200: bool = False
    above_ma50: bool = False
    bull_alignment: bool = False
    dist_ma20_min: Optional[float] = None
    dist_ma20_max: Optional[float] = None
    rsi_min: Optional[float] = None
    rsi_max: Optional[float] = None
    vol_ratio_min: Optional[float] = None
    near_20d_high: bool = False
    atr_pct_min: Optional[float] = None
    pct_chg_min: Optional[float] = None
    pct_chg_max: Optional[float] = None
    market: str = "US"
    tier: str = "all"
    limit: int = 50


class PositionInput(BaseModel):
    ticker: str
    cost_basis: Optional[float] = None
    shares: Optional[float] = None


class AnalyzeRequest(BaseModel):
    skill: str  # longterm_screen | swing_screen | position_review
    tickers: Optional[list[str]] = None
    ticker: Optional[str] = None
    cost_basis: Optional[float] = None
    shares: Optional[float] = None
    positions: Optional[list[PositionInput]] = None
    model: Optional[str] = None


# ── 页面 ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {
        "active_nav": "Home",
        "status": _get_db_status(),
        "last_update": _get_last_update(),
        "ai_models": _get_ai_models(),
    })


@app.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request, sector: str = ""):
    watchlist = _get_watchlist()
    sectors = _get_sectors(watchlist)
    if sector:
        watchlist = [t for t in watchlist if (t.get("sector") or "") == sector]
    return templates.TemplateResponse(request, "index.html", {
        "active_nav": "Watchlist",
        "watchlist": watchlist,
        "sectors": sectors,
        "selected": sector,
        "status": _get_db_status(),
    })


@app.get("/screener", response_class=HTMLResponse)
async def screener(request: Request, market: str = "US"):
    market = (market or "US").upper().strip()
    if market not in MARKETS:
        market = "US"
    return templates.TemplateResponse(request, "screener.html", {
        "active_nav": "Screener",
        "sectors": _get_all_sectors(market),
    })


@app.get("/positions", response_class=HTMLResponse)
async def positions_page(request: Request):
    return templates.TemplateResponse(request, "analysis.html",
                                      {"active_nav": "Positions"})


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    return templates.TemplateResponse(request, "history.html",
                                      {"active_nav": "History"})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html",
                                      {"active_nav": "Settings"})


# ── 持仓：全部转给 tools/positions.py ─────────────────────────────────────────

@app.get("/api/positions")
async def get_positions():
    """列持仓。盈亏、分币种汇总都在工具里算好了，这里只补一个市值字段。

    工具返回的 summary 是 ``{"USD": {...}, "CNY": {...}}`` —— 美元和人民币分开，
    没有跨币种的总数，因为那种数字算出来没意义。
    """
    data = _tool_json(positions_tool.list_positions())
    if data.get("error"):
        return JSONResponse({"positions": [], "summary": {},
                             "error": data["error"]})

    rows = data.get("positions") or []
    for row in rows:
        close, shares = row.get("close"), row.get("shares")
        row["market_value"] = (round(float(close) * float(shares), 2)
                               if close is not None and shares is not None
                               else None)
    return JSONResponse({"positions": rows, "summary": data.get("summary") or {}})


@app.post("/api/positions/add")
async def add_position(ticker: str = Form(...), avg_cost: float = Form(...),
                       shares: float = Form(...), market: str = Form("US")):
    result = _tool_json(positions_tool.add_position(
        ticker.strip().upper(), avg_cost, shares,
        (market or "US").strip().upper()))
    if result.get("error"):
        return JSONResponse({"ok": False, "error": result["error"]})
    result.setdefault("ok", True)
    return JSONResponse(result)


@app.post("/api/positions/remove")
async def remove_position(ticker: str = Form(...)):
    result = _tool_json(positions_tool.remove_position(ticker.strip().upper()))
    if result.get("error"):
        return JSONResponse({"ok": False, "error": result["error"]})
    result.setdefault("ok", True)
    return JSONResponse(result)


# ── 自选：全部转给 tools/watchlist.py ─────────────────────────────────────────

@app.post("/ticker/add")
async def add_ticker(ticker: str = Form(...)):
    """加自选。支持一次贴一串（逗号、空格、分号分隔）。

    以前这里会先去外部行情源现查一遍公司信息、顺手写进 stocks_meta ——
    那是采集，现在归 tt-data-hub。库里没有的票直接拒绝，由工具给出提示。
    """
    raw = (ticker or "").strip().upper()
    tickers = [t for t in re.split(r"[,\s;]+", raw) if t]
    if not tickers:
        return JSONResponse({"ok": False, "error": "没给有效的股票代码"})

    added, errors = [], []
    for one in tickers:
        result = _tool_json(watchlist_tool.add_to_watchlist(one))
        if result.get("error"):
            errors.append(f"{one}: {result['error']}")
        else:
            added.append({"ticker": one,
                          "already_present": bool(result.get("already_present"))})

    if len(tickers) == 1:
        if not added:
            return JSONResponse({"ok": False,
                                 "error": errors[0] if errors else "添加失败"})
        one = added[0]["ticker"]
        meta = {}
        try:
            meta = pg.query_one(
                "select company_name, sector from stocks_meta where ticker = %s",
                (one,)) or {}
        except Exception:
            pass
        return JSONResponse({"ok": True, "ticker": one,
                             "name": meta.get("company_name") or one,
                             "sector": meta.get("sector") or "",
                             "already_present": added[0]["already_present"]})

    return JSONResponse({
        "ok": len(added) > 0,
        "added": added,
        "errors": errors,
        "message": f"已加入 {len(added)}/{len(tickers)}"
                   + (f"，{len(errors)} 只失败" if errors else ""),
    })


@app.post("/ticker/remove")
async def remove_ticker(ticker: str = Form(...)):
    result = _tool_json(
        watchlist_tool.remove_from_watchlist(ticker.strip().upper()))
    if result.get("error"):
        return JSONResponse({"ok": False, "error": result["error"]})
    result.setdefault("ok", True)
    return JSONResponse(result)


# ── 采集：已经整个搬到 tt-data-hub ───────────────────────────────────────────

@app.post("/update")
async def trigger_update():
    return _moved_to_hub("手动触发更新", "采集任务由服务器上的 tt-data-hub 定时跑。")


@app.get("/update/status")
async def update_status():
    return _moved_to_hub("更新进度",
                         "最近一次采集的结果看首页，或者 /api/update/last。")


@app.get("/update/stream")
async def update_stream():
    return _moved_to_hub("更新日志推送",
                         "采集日志在 tt-data-hub 那边，这里推不了。")


@app.get("/status")
async def get_status():
    return JSONResponse(_get_db_status())


@app.get("/api/update/last")
async def last_update():
    return JSONResponse(_get_last_update() or {"empty": True})


@app.get("/api/mcp-url")
async def mcp_url(request: Request):
    """给 Claude.ai 用的远程 MCP 地址。

    令牌只从配置里读（config 会自己加载环境变量），不去翻任何文件。
    """
    token = config.MCP_TOKEN
    if not token:
        return JSONResponse({
            "url": None,
            "message": "还没有 MCP 令牌。先用 python main.py 起一次服务生成。",
        })
    host = request.headers.get("host", f"localhost:{config.MCP_PORT}")
    scheme = "https" if request.url.scheme == "https" else "http"

    skill_prompts = {}
    try:
        from skills import SKILL_NAMES, get_lang, load_skill_prompt
        lang = get_lang()
        for name in SKILL_NAMES:
            try:
                skill_prompts[name] = load_skill_prompt(name, lang)
            except Exception:
                pass
    except Exception:
        pass
    return JSONResponse({"url": f"{scheme}://{host}/mcp?token={token}",
                         "skills": skill_prompts})


# ── 筛选器 ────────────────────────────────────────────────────────────────────

# 行业分类的兜底跟 _ALL_SECTORS_SQL 是同一个理由：stocks_meta.sector 还没补全。
# 比较之前把两边都压成纯小写字母数字，「Health Care」和「Healthcare」就相等了；
# 换了词的那几组（Technology / Information Technology）靠 sector_candidates 展开。
_SCREEN_SQL = """
    with sector_src as (
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
        o.dist_ma20_pct, o.atr_pct,
        o.ma_20, o.ma_50, o.ma_200,
        (w.ticker is not null) as is_core
    from stock_ohlcv_daily o
    left join stocks_meta m on m.ticker = o.ticker
    left join sector_src ts on ts.ticker = o.ticker
    left join user_watchlist w on w.ticker = o.ticker
    where {where}
    order by o.vol_ratio desc nulls last
    limit %(limit)s
"""


def _build_screen_sql(req: ScreenRequest, as_of) -> tuple[str, dict]:
    """拼筛选 SQL。as_of 由调用方先查好传进来，这里不再算最新交易日。"""
    where = ["o.market = %(market)s", "o.date = %(as_of)s"]
    params: dict = {
        "market": req.market,
        "as_of": as_of,
        "limit": max(1, min(int(req.limit or 50), MAX_SCREEN_LIMIT)),
    }

    if req.sectors:
        # 每个板块名展开成所有等价写法，再合成一个数组一次比完
        candidates = sorted({c for s in req.sectors for c in sector_candidates(s)})
        where.append(
            "regexp_replace(lower(coalesce(m.sector, ts.sector)), "
            "'[^a-z0-9]', '', 'g') = any(%(sectors)s)")
        params["sectors"] = candidates

    if req.above_ma200:
        where.append("o.close > o.ma_200")
    if req.above_ma50:
        where.append("o.close > o.ma_50")
    if req.bull_alignment:
        where.append("o.ma_20 > o.ma_50 and o.ma_50 > o.ma_200")

    for field, op, column in (
        ("dist_ma20_min", ">=", "o.dist_ma20_pct"),
        ("dist_ma20_max", "<=", "o.dist_ma20_pct"),
        ("rsi_min", ">=", "o.rsi_14"),
        ("rsi_max", "<=", "o.rsi_14"),
        ("vol_ratio_min", ">=", "o.vol_ratio"),
        ("atr_pct_min", ">=", "o.atr_pct"),
        ("pct_chg_min", ">=", "o.pct_chg"),
        ("pct_chg_max", "<=", "o.pct_chg"),
    ):
        value = getattr(req, field)
        if value is not None:
            where.append(f"{column} {op} %({field})s")
            params[field] = value

    if req.near_20d_high:
        where.append("o.high_20 is not null and o.close / o.high_20 >= 0.95")

    # tier：core = 在自选池里，universe = 不在。线上 stocks_meta 没有 tier 列，
    # 自选池就是这个项目里「核心票」唯一的含义。
    if req.tier == "core":
        where.append("w.ticker is not null")
    elif req.tier == "universe":
        where.append("w.ticker is null")

    return _SCREEN_SQL.format(where=" and ".join(where)), params


def _sector_source_note(rows: list[dict]) -> str:
    """说清楚这批结果的行业分类是哪来的，免得以为板块筛选已经完全可用。"""
    from_meta = sum(1 for r in rows if r.get("_sector_from_meta"))
    from_fallback = sum(1 for r in rows
                        if not r.get("_sector_from_meta") and r.get("sector"))
    if from_meta and from_fallback:
        return (f"stocks_meta.sector 与 ticker_over20b 混合"
                f"（{from_meta} 只来自前者，{from_fallback} 只来自后者）")
    if from_meta:
        return "stocks_meta.sector"
    if from_fallback:
        return "ticker_over20b（临时兜底，等 tt-data-hub 补齐 stocks_meta.sector）"
    return "无（这批结果没有行业分类）"


@app.post("/screen")
async def screen(req: ScreenRequest):
    """多维筛选。**没有沿用 MCP 工具 screen_stocks**，理由见模块顶上的说明。"""
    market = (req.market or "US").upper().strip()
    if market not in MARKETS:
        return JSONResponse({
            "results": [], "total": 0, "date": None, "market": market,
            "error": f"market 只能是 {' 或 '.join(MARKETS)}；"
                     f"日线表混了两个市场，必须挑一个筛。",
        })
    req.market = market

    # 第一段：最新交易日单独查（吃索引，代价 1.43）。这个市场没数据就直接短路。
    try:
        as_of = latest_trading_day(market)
    except Exception as exc:
        return JSONResponse({"results": [], "total": 0, "date": None,
                             "market": market, "error": str(exc)})
    if as_of is None:
        return JSONResponse({"results": [], "total": 0, "date": None,
                             "market": market,
                             "note": f"{market} 市场的日线表还没有数据"})

    # 第二段：把那一天当常量传进主查询
    sql, params = _build_screen_sql(req, as_of)
    try:
        rows = pg.query(sql, params)
    except Exception as exc:
        return JSONResponse({"results": [], "total": 0,
                             "date": str(as_of), "market": market,
                             "error": str(exc)})

    note = _sector_source_note(rows)
    for row in rows:
        row.pop("_sector_from_meta", None)

    return JSONResponse(content=json.loads(json.dumps({
        "results": rows,
        "total": len(rows),
        "date": str(as_of),
        "market": market,
        "sector_source": note,
    }, default=str)))


# ── 个股详情 ──────────────────────────────────────────────────────────────────

_DETAIL_META_SQL = """
    select company_name, sector, industry, market, currency, latest_price
    from stocks_meta
    where ticker = %s
"""

# 基本面表主键是 (ticker, updated_at)，一只票十几行，只要最新那条。
_DETAIL_FUND_SQL = """
    select pe_ratio, ps_ratio, pb_ratio, peg_ratio, market_cap,
           revenue_growth_yoy, earnings_growth_yoy, gross_margin,
           roe, fcf_yield, operating_margin, profit_margin,
           forward_pe, ev_to_ebitda, beta, dividend_yield,
           week_52_high, week_52_low, short_interest_pct,
           analyst_rating, analyst_target_price, analyst_count,
           next_earnings_date, last_earnings_date, mspr,
           updated_at
    from stock_fundamentals
    where ticker = %s
    order by updated_at desc
    limit 1
"""

# 线上 news 表跟老的 DuckDB 版长得不一样：**没有 ticker 列、也没有 headline 列**。
# 标题在 title 里，正文在 content 里，关联的股票放在 related_tickers 这个文本数组
# 列里（上面建了 GIN 索引）。所以要用数组运算符去查才吃得到索引 —— 写成
# ``where ticker = %s`` 直接报「列不存在」。这里用重叠运算符 &&，跟
# skills/__init__.py 里那份新闻查询保持一致（&& 和 @> 在单元素数组上等价，
# 实测执行计划一模一样，都是走 idx_news_related_tickers 的位图索引扫描）。
# 前端读的字段名是 headline，所以在这里改名，不要让页面去迁就列名。
_DETAIL_NEWS_SQL = """
    select title as headline,
           coalesce(summary_cn, left(content, 400)) as summary,
           source, url, published_at, sentiment_label, sentiment_score
    from news
    where related_tickers && %s::text[]
      and published_at >= now() - interval '14 days'
    order by published_at desc
    limit 20
"""


@app.get("/ticker/{ticker}")
async def ticker_detail(ticker: str):
    """个股详情：公司信息 + 最新基本面 + 近两周新闻。

    以前库里查不到会现场调数据源采一份再写回库。那是采集，现在归 tt-data-hub，
    所以这里查不到就是查不到 —— 这个端点一个字都不往库里写。
    """
    ticker = ticker.strip().upper()
    try:
        meta = pg.query_one(_DETAIL_META_SQL, (ticker,)) or {}
        fundamentals = pg.query_one(_DETAIL_FUND_SQL, (ticker,))
        news = pg.query(_DETAIL_NEWS_SQL, ([ticker],))
    except Exception as exc:
        return JSONResponse({"ticker": ticker, "error": str(exc),
                             "fundamentals": None, "news": []})

    return JSONResponse(content=json.loads(json.dumps({
        "ticker": ticker,
        "company_name": meta.get("company_name") or "",
        "sector": meta.get("sector") or "",
        "industry": meta.get("industry") or "",
        "market": meta.get("market"),
        "currency": meta.get("currency"),
        "latest_price": meta.get("latest_price"),
        "fundamentals": fundamentals,
        "news": news,
    }, default=str)))


# ── AI 分析 ───────────────────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    try:
        from ai_client import _chat, get_model
        model_cfg = get_model(model_id=req.model, role="analysis")
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"取 AI 模型配置失败：{exc}"},
                            status_code=503)
    if not model_cfg:
        return JSONResponse({"ok": False, "error": "还没有配置 AI 模型"},
                            status_code=503)

    try:
        messages, _ = _build_analysis_messages(req)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    try:
        result = _chat(model_cfg, messages, max_tokens=4096, temperature=0.3)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    # 原来这里会把每次分析写进 analysis_history。线上没有这张表，而且写库这件事
    # 现在只允许发生在自选和持仓上，所以直接不存了（见 /analyze/history）。
    return JSONResponse({"ok": True, "skill": req.skill, "analysis": result})


def _build_analysis_messages(req: AnalyzeRequest) -> tuple[list[dict], dict]:
    """把技能提示词和行情上下文拼成一次对话。技能名不认就抛 ValueError。"""
    from skills import SKILL_NAMES, gather_context, get_lang, load_skill_prompt

    if req.skill not in SKILL_NAMES:
        raise ValueError(f"没有这个技能：{req.skill}")

    lang = get_lang()
    skill_prompt = load_skill_prompt(req.skill, lang)

    params: dict = {}
    if req.tickers:
        params["tickers"] = [t.upper() for t in req.tickers]
    if req.positions:
        params["positions"] = [{"ticker": p.ticker, "cost_basis": p.cost_basis,
                                "shares": p.shares} for p in req.positions]
    elif req.ticker:
        params["positions"] = [{"ticker": req.ticker,
                                "cost_basis": req.cost_basis,
                                "shares": req.shares}]

    context = gather_context(req.skill, params)
    resp_lang = "Chinese" if lang == "cn" else "English"
    messages = [
        {"role": "system",
         "content": f"You are a professional stock analyst. Follow the analysis "
                    f"workflow below precisely. Respond in {resp_lang}.\n\n"
                    f"{skill_prompt}"},
        {"role": "user",
         "content": f"Based on the following market data, perform the "
                    f"analysis:\n\n{context}"},
    ]
    return messages, params


@app.post("/analyze/stream")
async def analyze_stream(req: AnalyzeRequest):
    import asyncio

    from starlette.responses import StreamingResponse

    def sse_error(message: str):
        async def gen():
            yield f"data: {json.dumps({'error': message}, ensure_ascii=False)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    try:
        from ai_client import chat_stream, get_model
        model_cfg = get_model(model_id=req.model, role="analysis")
    except Exception as exc:
        return sse_error(f"取 AI 模型配置失败：{exc}")
    if not model_cfg:
        return sse_error("还没有配置 AI 模型，去设置页加一个。")

    try:
        messages, _ = _build_analysis_messages(req)
    except Exception as exc:
        return sse_error(str(exc))

    async def event_generator():
        yield ("data: " + json.dumps({"meta": {
            "model": model_cfg["display_name"],
            "supports_thinking": model_cfg.get("supports_thinking", False),
        }}, ensure_ascii=False) + "\n\n")
        try:
            for item in chat_stream(model_cfg, messages):
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)
        except Exception as exc:
            yield ("data: " + json.dumps({"type": "error", "chunk": str(exc)},
                                         ensure_ascii=False) + "\n\n")
        yield "event: done\ndata: ok\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/analyze/history")
async def analyze_history(skill: str = None, limit: int = 20):
    """历史分析记录。

    原来存在本地 DuckDB 的 analysis_history 表里，线上库没有这张表，而且这个项目
    现在只允许往自选和持仓写，所以分析结果不再落库。返回空列表让页面正常显示
    「暂无记录」，而不是抛错。
    """
    return JSONResponse({
        "history": [],
        "note": "分析记录不再落库（只读消费端只往自选和持仓写）。",
    })


@app.get("/analyze/history/{history_id}")
async def analyze_history_detail(history_id: int):
    return JSONResponse({
        "ok": False,
        "error": "分析记录不再落库，没有可查的历史。",
    }, status_code=404)


@app.get("/analyze/status")
async def analyze_status():
    """AI 可用性 + 技能列表。任何一块取不到都不能连累页面。"""
    payload = {"analysis_available": False, "sentiment_available": False,
               "skills": [], "models": [], "lang": "en"}
    try:
        from ai_client import analysis_available, list_models, sentiment_available
        payload["analysis_available"] = analysis_available()
        payload["sentiment_available"] = sentiment_available()
        payload["models"] = list_models()
    except Exception as exc:
        payload["error"] = str(exc)
    try:
        from skills import SKILL_NAMES, get_lang
        payload["skills"] = SKILL_NAMES
        payload["lang"] = get_lang()
    except Exception:
        pass
    return JSONResponse(payload)


# ── 设置：这几项的存储表线上都没有 ────────────────────────────────────────────

@app.get("/settings/lang")
async def get_language():
    try:
        from skills import SUPPORTED_LANGS, get_lang
        return JSONResponse({"lang": get_lang(), "supported": SUPPORTED_LANGS})
    except Exception as exc:
        return JSONResponse({"lang": "en", "supported": ["en", "cn"],
                            "error": str(exc)})


@app.post("/settings/lang")
async def set_language(request: Request):
    return JSONResponse({
        "ok": False,
        "error": "语言偏好原来存在本地库的 app_settings 表里，线上没有这张表。"
                 "现在改用环境变量 LANG_UI（en / cn）。",
    }, status_code=410)


@app.get("/settings/prompts")
async def get_prompts():
    """技能提示词。自定义覆盖原来存在 skill_prompts 表里，线上没有这张表，
    所以只返回内置的默认提示词。"""
    try:
        from skills import SKILL_NAMES, get_lang, load_default_skill_prompt
        lang = get_lang()
        result = {}
        for skill in SKILL_NAMES:
            default = load_default_skill_prompt(skill, lang)
            result[skill] = {"default": default, "custom": "",
                             "active": default, "is_custom": False}
        return JSONResponse({"prompts": result, "lang": lang,
                             "note": "自定义提示词暂不可用，改提示词请直接改 "
                                     "skills/ 下的 md 文件。"})
    except Exception as exc:
        return JSONResponse({"prompts": {}, "lang": "en", "error": str(exc)})


@app.post("/settings/prompts")
async def save_prompt(request: Request):
    return JSONResponse({
        "ok": False,
        "error": "自定义提示词原来存在本地库的 skill_prompts 表里，线上没有这张表。"
                 "改提示词请直接改 skills/ 下对应的 md 文件。",
    }, status_code=410)


@app.post("/settings/prompts/reset")
async def reset_prompt(request: Request):
    return JSONResponse({
        "ok": False,
        "error": "没有自定义提示词可重置，当前用的就是 skills/ 下的默认文件。",
    }, status_code=410)


# ── AI 模型配置：ai_models 表线上没有 ─────────────────────────────────────────

class ModelConfig(BaseModel):
    id: str
    display_name: str
    api_base: str
    api_key: Optional[str] = ""
    model_id: str
    api_format: str = "openai"  # openai | anthropic
    role: str = "both"  # sentiment | analysis | both
    supports_thinking: bool = False
    is_default_sentiment: bool = False
    is_default_analysis: bool = False


@app.get("/models")
async def get_models():
    return JSONResponse({"models": _get_ai_models(),
                         "note": "模型配置表线上没有，模型现在从环境变量读。"})


@app.post("/models")
async def add_model(cfg: ModelConfig):
    return JSONResponse({
        "ok": False,
        "error": "模型配置原来存在本地库的 ai_models 表里，线上没有这张表。"
                 "现在用环境变量 AI_API_BASE / AI_API_KEY / AI_MODEL 配。",
    }, status_code=410)


@app.delete("/models/{model_id}")
async def delete_model(model_id: str):
    return JSONResponse({
        "ok": False,
        "error": "模型配置表线上没有，没有可删的记录。",
    }, status_code=410)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.app:app", host="0.0.0.0", port=8765, reload=False)
