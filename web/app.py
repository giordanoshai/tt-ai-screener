import json
import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.init import get_conn, init_db

app = FastAPI(title="TT AI Screener")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bg_fetch_ohlcv(tickers: list[str]):
    """Background fetch OHLCV + fundamentals for newly added tickers."""
    try:
        from data_sources.registry import ProviderRegistry
        from db.update import update_ohlcv, update_fundamentals, update_analyst_data
        registry = ProviderRegistry()
        update_ohlcv(tickers, registry)
        update_fundamentals(tickers, registry)
        update_analyst_data(tickers, registry)
    except Exception as e:
        print(f"[bg_fetch] Error: {e}")


def _get_ai_models():
    con = get_conn()
    rows = con.execute("""
        SELECT display_name, model_id, role, is_default_sentiment, is_default_analysis
        FROM ai_models WHERE enabled = TRUE
        ORDER BY display_name
    """).fetchall()
    con.close()
    return [{"display_name": r[0], "model_id": r[1], "role": r[2],
             "is_default_sentiment": r[3], "is_default_analysis": r[4]} for r in rows]


def _get_watchlist():
    con = get_conn()
    rows = con.execute("""
        SELECT w.ticker, m.company_name, m.sector, m.industry,
               o.close, o.rsi_14, o.pct_chg,
               w.added_at
        FROM user_watchlist w
        LEFT JOIN stocks_meta m ON w.ticker = m.ticker
        LEFT JOIN stock_ohlcv_daily o ON w.ticker = o.ticker
            AND o.date = (SELECT MAX(date) FROM stock_ohlcv_daily WHERE ticker = w.ticker)
        ORDER BY m.sector, w.ticker
    """).fetchall()
    con.close()
    cols = ["ticker","company_name","sector","industry","close","rsi_14","pct_chg","added_at"]
    return [dict(zip(cols, r)) for r in rows]


def _get_sectors():
    con = get_conn()
    rows = con.execute("""
        SELECT DISTINCT m.sector
        FROM user_watchlist w
        JOIN stocks_meta m ON w.ticker = m.ticker
        WHERE m.sector IS NOT NULL AND m.sector != ''
        ORDER BY m.sector
    """).fetchall()
    con.close()
    return [r[0] for r in rows]


def _get_all_sectors():
    con = get_conn()
    rows = con.execute("""
        SELECT sector, COUNT(*) as cnt
        FROM stocks_meta
        WHERE sector IS NOT NULL AND sector != ''
        GROUP BY sector
        ORDER BY cnt DESC
    """).fetchall()
    con.close()
    return [r[0] for r in rows]


def _get_db_status():
    con = get_conn()
    ohlcv  = con.execute("SELECT COUNT(*), COUNT(DISTINCT ticker), MAX(date) FROM stock_ohlcv_daily").fetchone()
    news   = con.execute("SELECT COUNT(*), MAX(published_at)::DATE FROM news").fetchone()
    watch  = con.execute("SELECT COUNT(*) FROM user_watchlist").fetchone()
    con.close()
    return {
        "tickers":       watch[0],
        "ohlcv_rows":    ohlcv[0],
        "ohlcv_tickers": ohlcv[1],
        "ohlcv_latest":  str(ohlcv[2]) if ohlcv[2] else "—",
        "news_count":    news[0],
        "news_latest":   str(news[1]) if news[1] else "—",
    }


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
    shares: Optional[int] = None


class AnalyzeRequest(BaseModel):
    skill: str  # longterm_screen | swing_screen | position_review
    tickers: Optional[list[str]] = None
    ticker: Optional[str] = None
    cost_basis: Optional[float] = None
    shares: Optional[int] = None
    positions: Optional[list[PositionInput]] = None
    model: Optional[str] = None  # model id override


# ── Routes ────────────────────────────────────────────────────────────────────

def _get_last_update():
    con = get_conn()
    row = con.execute("""
        SELECT started_at, finished_at, status, duration_s, summary
        FROM update_history ORDER BY id DESC LIMIT 1
    """).fetchone()
    con.close()
    if not row:
        return None
    import json as _json
    return {
        "started_at": str(row[0]) if row[0] else None,
        "finished_at": str(row[1]) if row[1] else None,
        "status": row[2],
        "duration_s": row[3],
        "summary": _json.loads(row[4]) if row[4] else {},
    }


def _get_schedule():
    con = get_conn()
    row = con.execute("SELECT value FROM app_settings WHERE key = 'update_schedule'").fetchone()
    con.close()
    if row:
        import json as _json
        return _json.loads(row[0])
    return {"enabled": False, "time": "17:30"}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    status = _get_db_status()
    last_update = _get_last_update()
    schedule = _get_schedule()
    ai_models = _get_ai_models()
    return templates.TemplateResponse(request, "home.html", {
        "active_nav": "Home",
        "status": status,
        "last_update": last_update,
        "schedule": schedule,
        "ai_models": ai_models,
    })


@app.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request, sector: str = ""):
    watchlist = _get_watchlist()
    sectors   = _get_sectors()
    status    = _get_db_status()
    if sector:
        watchlist = [t for t in watchlist if (t["sector"] or "") == sector]
    return templates.TemplateResponse(request, "index.html", {
        "active_nav": "Watchlist",
        "watchlist": watchlist,
        "sectors":   sectors,
        "selected":  sector,
        "status":    status,
    })


@app.get("/screener", response_class=HTMLResponse)
async def screener(request: Request):
    sectors = _get_all_sectors()
    return templates.TemplateResponse(request, "screener.html", {
        "active_nav": "Screener",
        "sectors": sectors,
    })


@app.get("/positions", response_class=HTMLResponse)
async def positions_page(request: Request):
    return templates.TemplateResponse(request, "analysis.html", {"active_nav": "Positions"})


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    return templates.TemplateResponse(request, "history.html", {"active_nav": "History"})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {"active_nav": "Settings"})


@app.get("/api/positions")
async def get_positions():
    con = get_conn()
    rows = con.execute("""
        SELECT p.ticker, p.avg_cost, p.shares, p.added_at,
               m.company_name, m.sector,
               o.close
        FROM user_positions p
        LEFT JOIN stocks_meta m ON p.ticker = m.ticker
        LEFT JOIN stock_ohlcv_daily o ON p.ticker = o.ticker
            AND o.date = (SELECT MAX(date) FROM stock_ohlcv_daily WHERE ticker = p.ticker)
        ORDER BY p.ticker
    """).fetchall()
    con.close()
    cols = ["ticker","avg_cost","shares","added_at","company_name","sector","close"]
    results = []
    for r in rows:
        d = dict(zip(cols, r))
        d["added_at"] = str(d["added_at"]) if d.get("added_at") else None
        if d["close"] and d["avg_cost"]:
            d["pnl_pct"] = round((d["close"] - d["avg_cost"]) / d["avg_cost"] * 100, 2)
            d["pnl_amount"] = round((d["close"] - d["avg_cost"]) * (d["shares"] or 0), 2)
            d["market_value"] = round(d["close"] * (d["shares"] or 0), 2)
        else:
            d["pnl_pct"] = None
            d["pnl_amount"] = None
            d["market_value"] = None
        results.append(d)
    return JSONResponse({"positions": results})


@app.post("/api/positions/add")
async def add_position(ticker: str = Form(...), avg_cost: float = Form(...), shares: int = Form(...)):
    ticker = ticker.strip().upper()
    if not ticker or avg_cost <= 0 or shares <= 0:
        return JSONResponse({"ok": False, "error": "Invalid input"})
    con = get_conn()
    con.execute("""
        INSERT INTO user_positions (ticker, avg_cost, shares)
        VALUES (?, ?, ?)
        ON CONFLICT (ticker) DO UPDATE SET
            avg_cost = EXCLUDED.avg_cost,
            shares = EXCLUDED.shares
    """, [ticker, avg_cost, shares])
    con.close()
    return JSONResponse({"ok": True, "ticker": ticker})


@app.post("/api/positions/remove")
async def remove_position(ticker: str = Form(...)):
    ticker = ticker.strip().upper()
    con = get_conn()
    con.execute("DELETE FROM user_positions WHERE ticker = ?", [ticker])
    con.close()
    return JSONResponse({"ok": True})


@app.post("/ticker/add")
async def add_ticker(ticker: str = Form(...)):
    import re
    raw = ticker.strip().upper()
    if not raw:
        return JSONResponse({"ok": False, "error": "Empty ticker"})

    tickers = [t.strip() for t in re.split(r'[,\s;]+', raw) if t.strip()]
    if not tickers:
        return JSONResponse({"ok": False, "error": "No valid tickers"})

    added = []
    errors = []
    con = get_conn()
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            name     = info.get("longName") or info.get("shortName") or t
            sector   = info.get("sector", "")
            industry = info.get("industry", "")
            exchange = info.get("exchange", "")

            if not info.get("regularMarketPrice") and not info.get("currentPrice"):
                errors.append(f"{t}: not found")
                continue

            con.execute("""
                INSERT INTO stocks_meta (ticker, company_name, exchange, sector, industry)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (ticker) DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    sector = EXCLUDED.sector,
                    industry = EXCLUDED.industry
            """, [t, name, exchange, sector, industry])
            con.execute("""
                INSERT INTO user_watchlist (ticker, added_at)
                VALUES (?, ?)
                ON CONFLICT (ticker) DO NOTHING
            """, [t, datetime.now()])
            added.append({"ticker": t, "name": name, "sector": sector})
        except Exception as e:
            errors.append(f"{t}: {e}")
    con.close()

    if added:
        import threading
        added_tickers = [a["ticker"] for a in added]
        threading.Thread(target=_bg_fetch_ohlcv, args=(added_tickers,), daemon=True).start()

    if len(tickers) == 1:
        if added:
            a = added[0]
            return JSONResponse({"ok": True, "ticker": a["ticker"], "name": a["name"], "sector": a["sector"]})
        else:
            return JSONResponse({"ok": False, "error": errors[0] if errors else "Failed"})

    return JSONResponse({
        "ok": len(added) > 0,
        "added": added,
        "errors": errors,
        "message": f"Added {len(added)}/{len(tickers)}" + (f", {len(errors)} failed" if errors else ""),
    })


@app.post("/ticker/remove")
async def remove_ticker(ticker: str = Form(...)):
    ticker = ticker.strip().upper()
    try:
        con = get_conn()
        con.execute("DELETE FROM user_watchlist WHERE ticker = ?", [ticker])
        con.close()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/update")
async def trigger_update():
    import threading
    from db.update import run as run_update, get_update_state
    state = get_update_state()
    if state["running"]:
        return JSONResponse({"ok": False, "error": "Update already running", "stage": state["stage"]})
    threading.Thread(target=run_update, daemon=True, name="data-update").start()
    return JSONResponse({"ok": True, "message": "Update started"})


@app.get("/update/status")
async def update_status():
    from db.update import get_update_state
    return JSONResponse(get_update_state())


@app.get("/update/stream")
async def update_stream():
    """SSE endpoint for real-time update progress."""
    import asyncio
    from starlette.responses import StreamingResponse
    from db.update import get_update_state

    async def event_generator():
        last_len = 0
        while True:
            state = get_update_state()
            log = state["log"]
            if len(log) > last_len:
                for line in log[last_len:]:
                    yield f"data: {line}\n\n"
                last_len = len(log)
            if not state["running"] and last_len > 0:
                yield f"event: done\ndata: {state.get('finished_at', '')}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/status")
async def get_status():
    return JSONResponse(_get_db_status())


@app.get("/api/mcp-url")
async def mcp_url(request: Request):
    from config import MCP_TOKEN, MCP_PORT
    token = MCP_TOKEN
    if not token:
        env_file = Path(__file__).parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("MCP_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break
    if not token:
        return JSONResponse({"url": None, "message": "MCP token not generated yet. Start the server with 'python main.py' first."})
    host = request.headers.get("host", f"localhost:{MCP_PORT}")
    scheme = "https" if request.url.scheme == "https" else "http"
    url = f"{scheme}://{host}/mcp?token={token}"
    from skills import load_skill_prompt, SKILL_NAMES, get_lang
    lang = get_lang()
    skill_prompts = {}
    for name in SKILL_NAMES:
        try:
            skill_prompts[name] = load_skill_prompt(name, lang)
        except Exception:
            pass
    return JSONResponse({"url": url, "skills": skill_prompts})


@app.get("/api/update/last")
async def last_update():
    data = _get_last_update()
    return JSONResponse(data or {"empty": True})


@app.get("/settings/schedule")
async def get_schedule():
    return JSONResponse(_get_schedule())


@app.post("/settings/schedule")
async def set_schedule(request: Request):
    body = await request.json()
    enabled = bool(body.get("enabled", False))
    cron_time = body.get("time", "17:30")
    import re
    if not re.match(r"^\d{1,2}:\d{2}$", cron_time):
        return JSONResponse({"ok": False, "error": "Invalid time format, use HH:MM"}, status_code=400)
    import json as _json
    schedule = {"enabled": enabled, "time": cron_time}
    con = get_conn()
    con.execute("""
        INSERT INTO app_settings (key, value) VALUES ('update_schedule', ?)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, [_json.dumps(schedule)])
    con.close()

    from main import apply_schedule
    apply_schedule(schedule)

    return JSONResponse({"ok": True, "schedule": schedule})


@app.post("/screen")
async def screen_stocks(req: ScreenRequest):
    con = get_conn()
    try:
        max_date_row = con.execute("SELECT MAX(date) FROM stock_ohlcv_daily").fetchone()
        if not max_date_row or not max_date_row[0]:
            return JSONResponse({"results": [], "total": 0, "date": None})
        max_date = max_date_row[0]

        where = ["o.date = ?"]
        params: list = [max_date]

        if req.sectors:
            placeholders = ",".join(["?"] * len(req.sectors))
            where.append(f"m.sector IN ({placeholders})")
            params.extend(req.sectors)

        if req.above_ma200:
            where.append("o.close > o.ma_200")
        if req.above_ma50:
            where.append("o.close > o.ma_50")
        if req.bull_alignment:
            where.append("o.ma_20 > o.ma_50 AND o.ma_50 > o.ma_200")

        if req.dist_ma20_min is not None:
            where.append("o.dist_ma20_pct >= ?")
            params.append(req.dist_ma20_min)
        if req.dist_ma20_max is not None:
            where.append("o.dist_ma20_pct <= ?")
            params.append(req.dist_ma20_max)

        if req.rsi_min is not None:
            where.append("o.rsi_14 >= ?")
            params.append(req.rsi_min)
        if req.rsi_max is not None:
            where.append("o.rsi_14 <= ?")
            params.append(req.rsi_max)

        if req.vol_ratio_min is not None:
            where.append("o.vol_ratio >= ?")
            params.append(req.vol_ratio_min)

        if req.near_20d_high:
            where.append("o.high_20 IS NOT NULL AND o.close / o.high_20 >= 0.95")

        if req.atr_pct_min is not None:
            where.append("o.atr_pct >= ?")
            params.append(req.atr_pct_min)

        if req.pct_chg_min is not None:
            where.append("o.pct_chg >= ?")
            params.append(req.pct_chg_min)
        if req.pct_chg_max is not None:
            where.append("o.pct_chg <= ?")
            params.append(req.pct_chg_max)

        if req.market != "all":
            where.append("COALESCE(m.market, 'US') = ?")
            params.append(req.market)

        if req.tier != "all":
            where.append("COALESCE(m.tier, 'core') = ?")
            params.append(req.tier)

        params.append(req.limit)

        sql = f"""
            SELECT
                o.ticker, m.company_name, m.sector,
                o.close, o.pct_chg, o.rsi_14, o.vol_ratio, o.dist_ma20_pct,
                CASE WHEN w.ticker IS NOT NULL THEN true ELSE false END AS is_core
            FROM stock_ohlcv_daily o
            LEFT JOIN stocks_meta m ON o.ticker = m.ticker
            LEFT JOIN user_watchlist w ON o.ticker = w.ticker
            WHERE {' AND '.join(where)}
            ORDER BY o.vol_ratio DESC NULLS LAST
            LIMIT ?
        """

        rows = con.execute(sql, params).fetchall()
        cols = ["ticker","company_name","sector","close","pct_chg","rsi_14","vol_ratio","dist_ma20_pct","is_core"]
        results = [dict(zip(cols, r)) for r in rows]
        return JSONResponse({"results": results, "total": len(results), "date": str(max_date)})
    finally:
        con.close()


@app.get("/ticker/{ticker}")
async def ticker_detail(ticker: str):
    ticker = ticker.strip().upper()
    con = get_conn()
    try:
        fund_row = con.execute("""
            SELECT pe_ratio, ps_ratio, pb_ratio, peg_ratio, market_cap,
                   revenue_growth_yoy, earnings_growth_yoy, gross_margin,
                   roe, fcf_yield,
                   analyst_rating, analyst_target_price, analyst_count,
                   next_earnings_date, last_earnings_date, mspr,
                   updated_at
            FROM stock_fundamentals
            WHERE ticker = ? AND updated_at >= CURRENT_DATE - INTERVAL '7 days'
        """, [ticker]).fetchone()

        fundamentals = None
        if fund_row:
            fund_cols = ["pe_ratio","ps_ratio","pb_ratio","peg_ratio","market_cap",
                         "revenue_growth_yoy","earnings_growth_yoy","gross_margin",
                         "roe","fcf_yield",
                         "analyst_rating","analyst_target_price","analyst_count",
                         "next_earnings_date","last_earnings_date","mspr",
                         "updated_at"]
            fundamentals = dict(zip(fund_cols, fund_row))
        else:
            try:
                from data_sources.registry import ProviderRegistry
                registry = ProviderRegistry()
                provider = registry.get_fundamentals_provider("US")
                data = provider.fetch_fundamentals(ticker)
                con.execute("""
                    INSERT INTO stock_fundamentals (
                        ticker, pe_ratio, ps_ratio, pb_ratio, peg_ratio, market_cap,
                        revenue_growth_yoy, earnings_growth_yoy, gross_margin,
                        roe, fcf_yield, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_DATE)
                    ON CONFLICT (ticker) DO UPDATE SET
                        pe_ratio=EXCLUDED.pe_ratio, ps_ratio=EXCLUDED.ps_ratio,
                        pb_ratio=EXCLUDED.pb_ratio, peg_ratio=EXCLUDED.peg_ratio,
                        market_cap=EXCLUDED.market_cap,
                        revenue_growth_yoy=EXCLUDED.revenue_growth_yoy,
                        earnings_growth_yoy=EXCLUDED.earnings_growth_yoy,
                        gross_margin=EXCLUDED.gross_margin,
                        roe=EXCLUDED.roe, fcf_yield=EXCLUDED.fcf_yield,
                        updated_at=EXCLUDED.updated_at
                """, [
                    ticker, data.get("pe_ratio"), data.get("ps_ratio"),
                    data.get("pb_ratio"), data.get("peg_ratio"), data.get("market_cap"),
                    data.get("revenue_growth_yoy"), data.get("earnings_growth_yoy"),
                    data.get("gross_margin"), data.get("roe"), data.get("fcf_yield"),
                ])
                fundamentals = {k: v for k, v in data.items()
                                if k in ["pe_ratio","ps_ratio","pb_ratio","peg_ratio","market_cap",
                                         "revenue_growth_yoy","earnings_growth_yoy","gross_margin",
                                         "roe","fcf_yield"]}
            except Exception:
                pass

        news_rows = con.execute("""
            SELECT headline, summary, source, published_at, sentiment_label
            FROM news
            WHERE ticker = ? AND published_at >= CURRENT_TIMESTAMP - INTERVAL '14 days'
            ORDER BY published_at DESC LIMIT 20
        """, [ticker]).fetchall()

        news = []
        if news_rows:
            news_cols = ["headline","summary","source","published_at","sentiment_label"]
            news = [dict(zip(news_cols, r)) for r in news_rows]
        else:
            try:
                from data_sources.registry import ProviderRegistry
                registry = ProviderRegistry()
                provider = registry.get_news_provider("US")
                articles = provider.fetch_news(ticker, days=14)
                for art in articles:
                    con.execute("""
                        INSERT INTO news (id, ticker, headline, summary, source, url, published_at, sentiment_label)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (id) DO NOTHING
                    """, [art["id"], ticker, art["headline"], art["summary"],
                          art["source"], art["url"], art["published_at"], art["sentiment_label"]])
                news_cols = ["headline","summary","source","published_at","sentiment_label"]
                news = [{k: art.get(k) for k in news_cols} for art in articles[:20]]
            except Exception:
                pass

        meta_row = con.execute(
            "SELECT company_name, sector, industry FROM stocks_meta WHERE ticker = ?", [ticker]
        ).fetchone()

        return JSONResponse(content=json.loads(json.dumps({
            "ticker": ticker,
            "company_name": meta_row[0] if meta_row else "",
            "sector": meta_row[1] if meta_row else "",
            "industry": meta_row[2] if meta_row else "",
            "fundamentals": fundamentals,
            "news": news,
        }, default=str)))

    finally:
        con.close()


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    from ai_client import get_model, _chat

    model_cfg = get_model(model_id=req.model, role="analysis")
    if not model_cfg:
        return JSONResponse({"ok": False, "error": "No AI model configured"}, status_code=503)

    try:
        from skills import load_skill_prompt, gather_context, SKILL_NAMES, get_lang
        if req.skill not in SKILL_NAMES:
            return JSONResponse({"ok": False, "error": f"Unknown skill: {req.skill}"}, status_code=400)

        lang = get_lang()
        skill_prompt = load_skill_prompt(req.skill, lang)
        params = {}
        if req.tickers:
            params["tickers"] = [t.upper() for t in req.tickers]
        if req.positions:
            params["positions"] = [
                {"ticker": p.ticker, "cost_basis": p.cost_basis, "shares": p.shares}
                for p in req.positions
            ]
        elif req.ticker:
            params["positions"] = [{"ticker": req.ticker, "cost_basis": req.cost_basis, "shares": req.shares}]

        context = gather_context(req.skill, params)
        resp_lang = "Chinese" if lang == "cn" else "English"
        messages = [
            {"role": "system", "content": f"You are a professional stock analyst. Follow the analysis workflow below precisely. Respond in {resp_lang}.\n\n{skill_prompt}"},
            {"role": "user", "content": f"Based on the following market data, perform the analysis:\n\n{context}"},
        ]
        result = _chat(model_cfg, messages, max_tokens=4096, temperature=0.3)

        try:
            ticker_list = params.get("tickers") or [p["ticker"] for p in params.get("positions", [])]
            con = get_conn()
            con.execute("""
                INSERT INTO analysis_history (skill, tickers, ticker_count, context_json, analysis_text, model)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [req.skill, ",".join(ticker_list) if ticker_list else None,
                  len(ticker_list) if ticker_list else 0, context, result, model_cfg["display_name"]])
            con.close()
        except Exception:
            pass

        return JSONResponse({"ok": True, "skill": req.skill, "analysis": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/analyze/stream")
async def analyze_stream(req: AnalyzeRequest):
    import asyncio
    from starlette.responses import StreamingResponse
    from ai_client import get_model, chat_stream

    model_cfg = get_model(model_id=req.model, role="analysis")
    if not model_cfg:
        async def err():
            yield f"data: {json.dumps({'error': 'No AI model configured. Add models via Settings.'})}\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    from skills import load_skill_prompt, gather_context, SKILL_NAMES, get_lang
    if req.skill not in SKILL_NAMES:
        async def err():
            yield f"data: {json.dumps({'error': f'Unknown skill: {req.skill}'})}\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    lang = get_lang()
    skill_prompt = load_skill_prompt(req.skill, lang)
    params = {}
    if req.tickers:
        params["tickers"] = [t.upper() for t in req.tickers]
    if req.positions:
        params["positions"] = [
            {"ticker": p.ticker, "cost_basis": p.cost_basis, "shares": p.shares}
            for p in req.positions
        ]
    elif req.ticker:
        params["positions"] = [{"ticker": req.ticker, "cost_basis": req.cost_basis, "shares": req.shares}]

    context = gather_context(req.skill, params)
    resp_lang = "Chinese" if lang == "cn" else "English"
    messages = [
        {"role": "system", "content": f"You are a professional stock analyst. Follow the analysis workflow below precisely. Respond in {resp_lang}.\n\n{skill_prompt}"},
        {"role": "user", "content": f"Based on the following market data, perform the analysis:\n\n{context}"},
    ]

    async def event_generator():
        # Send model info to frontend
        yield f"data: {json.dumps({'meta': {'model': model_cfg['display_name'], 'supports_thinking': model_cfg.get('supports_thinking', False)}})}\n\n"

        content_parts = []
        try:
            for item in chat_stream(model_cfg, messages):
                # item: {"type": "thinking"|"content", "chunk": "..."}
                if item["type"] == "content":
                    content_parts.append(item["chunk"])
                yield f"data: {json.dumps(item)}\n\n"
                await asyncio.sleep(0)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'chunk': str(e)})}\n\n"

        # Save to history (content only, not thinking)
        result_text = "".join(content_parts)
        if result_text:
            try:
                ticker_list = params.get("tickers") or [p["ticker"] for p in params.get("positions", [])]
                con = get_conn()
                con.execute("""
                    INSERT INTO analysis_history (skill, tickers, ticker_count, context_json, analysis_text, model)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, [
                    req.skill,
                    ",".join(ticker_list) if ticker_list else None,
                    len(ticker_list) if ticker_list else 0,
                    context, result_text, model_cfg["display_name"],
                ])
                con.close()
            except Exception:
                pass

        yield f"event: done\ndata: ok\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/analyze/history")
async def analyze_history(skill: str = None, limit: int = 20):
    con = get_conn()
    where = "1=1"
    params = []
    if skill:
        where += " AND skill = ?"
        params.append(skill)
    params.append(limit)
    rows = con.execute(f"""
        SELECT id, created_at, skill, tickers, ticker_count, analysis_text, model
        FROM analysis_history
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ?
    """, params).fetchall()
    con.close()
    cols = ["id","created_at","skill","tickers","ticker_count","analysis_text","model"]
    results = []
    for r in rows:
        d = dict(zip(cols, r))
        d["created_at"] = str(d["created_at"]) if d["created_at"] else None
        results.append(d)
    return JSONResponse({"history": results})


@app.get("/analyze/history/{history_id}")
async def analyze_history_detail(history_id: int):
    con = get_conn()
    row = con.execute(
        "SELECT id, created_at, skill, tickers, ticker_count, context_json, analysis_text, model FROM analysis_history WHERE id = ?",
        [history_id]
    ).fetchone()
    con.close()
    if not row:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    cols = ["id","created_at","skill","tickers","ticker_count","context_json","analysis_text","model"]
    d = dict(zip(cols, row))
    d["created_at"] = str(d["created_at"]) if d["created_at"] else None
    return JSONResponse(d)


@app.get("/analyze/status")
async def analyze_status():
    from ai_client import analysis_available, sentiment_available, list_models
    from skills import SKILL_NAMES, get_lang
    return JSONResponse({
        "analysis_available": analysis_available(),
        "sentiment_available": sentiment_available(),
        "skills": SKILL_NAMES,
        "models": list_models(),
        "lang": get_lang(),
    })


@app.get("/settings/lang")
async def get_language():
    from skills import get_lang, SUPPORTED_LANGS
    return JSONResponse({"lang": get_lang(), "supported": SUPPORTED_LANGS})


@app.post("/settings/lang")
async def set_language(request: Request):
    body = await request.json()
    lang = body.get("lang", "en")
    from skills import SUPPORTED_LANGS
    if lang not in SUPPORTED_LANGS:
        return JSONResponse({"ok": False, "error": f"Unsupported language: {lang}"}, status_code=400)
    con = get_conn()
    con.execute("""
        INSERT INTO app_settings (key, value) VALUES ('lang', ?)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, [lang])
    con.close()
    return JSONResponse({"ok": True, "lang": lang})


@app.get("/settings/prompts")
async def get_prompts():
    from skills import SKILL_NAMES, get_lang, load_skill_prompt, load_default_skill_prompt
    lang = get_lang()
    con = get_conn()
    rows = con.execute(
        "SELECT skill, lang, prompt FROM skill_prompts WHERE lang = ?", [lang]
    ).fetchall()
    con.close()
    custom = {r[0]: r[2] for r in rows}
    result = {}
    for skill in SKILL_NAMES:
        result[skill] = {
            "default": load_default_skill_prompt(skill, lang),
            "custom": custom.get(skill, ""),
            "active": custom.get(skill, "") or load_default_skill_prompt(skill, lang),
            "is_custom": skill in custom and custom[skill].strip() != "",
        }
    return JSONResponse({"prompts": result, "lang": lang})


@app.post("/settings/prompts")
async def save_prompt(request: Request):
    body = await request.json()
    skill = body.get("skill", "")
    prompt = body.get("prompt", "")
    from skills import SKILL_NAMES, get_lang
    if skill not in SKILL_NAMES:
        return JSONResponse({"ok": False, "error": f"Unknown skill: {skill}"}, status_code=400)
    lang = get_lang()
    con = get_conn()
    if prompt.strip():
        con.execute("""
            INSERT INTO skill_prompts (skill, lang, prompt, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (skill, lang) DO UPDATE SET
                prompt = EXCLUDED.prompt, updated_at = CURRENT_TIMESTAMP
        """, [skill, lang, prompt])
    else:
        con.execute("DELETE FROM skill_prompts WHERE skill = ? AND lang = ?", [skill, lang])
    con.close()
    return JSONResponse({"ok": True, "skill": skill, "lang": lang})


@app.post("/settings/prompts/reset")
async def reset_prompt(request: Request):
    body = await request.json()
    skill = body.get("skill", "")
    from skills import SKILL_NAMES, get_lang
    if skill not in SKILL_NAMES:
        return JSONResponse({"ok": False, "error": f"Unknown skill: {skill}"}, status_code=400)
    lang = get_lang()
    con = get_conn()
    con.execute("DELETE FROM skill_prompts WHERE skill = ? AND lang = ?", [skill, lang])
    con.close()
    return JSONResponse({"ok": True})


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
    con = get_conn()
    rows = con.execute("""
        SELECT id, display_name, api_base, model_id, api_format, role, supports_thinking,
               is_default_sentiment, is_default_analysis, enabled,
               CASE WHEN api_key IS NOT NULL AND api_key != '' THEN true ELSE false END AS has_key
        FROM ai_models ORDER BY display_name
    """).fetchall()
    con.close()
    cols = ["id","display_name","api_base","model_id","api_format","role","supports_thinking",
            "is_default_sentiment","is_default_analysis","enabled","has_key"]
    return JSONResponse({"models": [dict(zip(cols, r)) for r in rows]})


@app.post("/models")
async def add_model(cfg: ModelConfig):
    con = get_conn()
    if cfg.is_default_sentiment:
        con.execute("UPDATE ai_models SET is_default_sentiment = FALSE")
    if cfg.is_default_analysis:
        con.execute("UPDATE ai_models SET is_default_analysis = FALSE")
    # If api_key is empty string, keep existing key (don't overwrite)
    key_clause = "api_key=EXCLUDED.api_key" if cfg.api_key else "api_key=ai_models.api_key"
    con.execute(f"""
        INSERT INTO ai_models (id, display_name, api_base, api_key, model_id, api_format, role, supports_thinking, is_default_sentiment, is_default_analysis, enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
        ON CONFLICT (id) DO UPDATE SET
            display_name=EXCLUDED.display_name, api_base=EXCLUDED.api_base, {key_clause},
            model_id=EXCLUDED.model_id, api_format=EXCLUDED.api_format, role=EXCLUDED.role,
            supports_thinking=EXCLUDED.supports_thinking,
            is_default_sentiment=EXCLUDED.is_default_sentiment, is_default_analysis=EXCLUDED.is_default_analysis
    """, [cfg.id, cfg.display_name, cfg.api_base, cfg.api_key or "", cfg.model_id,
          cfg.api_format, cfg.role, cfg.supports_thinking, cfg.is_default_sentiment, cfg.is_default_analysis])
    con.close()
    return JSONResponse({"ok": True})


@app.delete("/models/{model_id}")
async def delete_model(model_id: str):
    con = get_conn()
    con.execute("DELETE FROM ai_models WHERE id = ?", [model_id])
    con.close()
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run("web.app:app", host="0.0.0.0", port=8765, reload=False)
