import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.init import get_conn, init_db

app = FastAPI(title="tt-trading-mcp")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, sector: str = ""):
    watchlist = _get_watchlist()
    sectors   = _get_sectors()
    status    = _get_db_status()
    if sector:
        watchlist = [t for t in watchlist if (t["sector"] or "") == sector]
    return templates.TemplateResponse(request, "index.html", {
        "watchlist": watchlist,
        "sectors":   sectors,
        "selected":  sector,
        "status":    status,
    })


@app.post("/ticker/add")
async def add_ticker(ticker: str = Form(...)):
    ticker = ticker.strip().upper()
    if not ticker:
        return JSONResponse({"ok": False, "error": "Empty ticker"})
    try:
        info = yf.Ticker(ticker).info
        name     = info.get("longName") or info.get("shortName") or ticker
        sector   = info.get("sector", "")
        industry = info.get("industry", "")
        exchange = info.get("exchange", "")

        if not info.get("regularMarketPrice") and not info.get("currentPrice"):
            return JSONResponse({"ok": False, "error": f"{ticker} not found on Yahoo Finance"})

        con = get_conn()
        con.execute("""
            INSERT INTO stocks_meta (ticker, company_name, exchange, sector, industry)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (ticker) DO UPDATE SET
                company_name = EXCLUDED.company_name,
                sector = EXCLUDED.sector,
                industry = EXCLUDED.industry
        """, [ticker, name, exchange, sector, industry])
        con.execute("""
            INSERT INTO user_watchlist (ticker, added_at)
            VALUES (?, ?)
            ON CONFLICT (ticker) DO NOTHING
        """, [ticker, datetime.now()])
        con.close()

        return JSONResponse({"ok": True, "ticker": ticker, "name": name, "sector": sector})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


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
    try:
        script = Path(__file__).parent.parent / "db" / "update.py"
        subprocess.Popen([sys.executable, str(script)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return JSONResponse({"ok": True, "message": "Update started in background"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/status")
async def get_status():
    return JSONResponse(_get_db_status())


if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run("web.app:app", host="0.0.0.0", port=8765, reload=False)
