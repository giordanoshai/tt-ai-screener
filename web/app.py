import json
import subprocess
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
                   roe, fcf_yield, updated_at
            FROM stock_fundamentals
            WHERE ticker = ? AND updated_at >= CURRENT_DATE - INTERVAL '7 days'
        """, [ticker]).fetchone()

        fundamentals = None
        if fund_row:
            fund_cols = ["pe_ratio","ps_ratio","pb_ratio","peg_ratio","market_cap",
                         "revenue_growth_yoy","earnings_growth_yoy","gross_margin",
                         "roe","fcf_yield","updated_at"]
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


if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run("web.app:app", host="0.0.0.0", port=8765, reload=False)
