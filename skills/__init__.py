"""Skills analysis engine — queries DB, assembles context, calls AI API."""
import json
from datetime import date
from pathlib import Path

from db.init import get_conn

SKILLS_DIR = Path(__file__).parent

SKILL_NAMES = ["longterm_screen", "swing_screen", "position_review"]
SUPPORTED_LANGS = ["en", "cn"]
DEFAULT_LANG = "en"


def get_lang() -> str:
    """Read language preference: DB setting > .env > default."""
    try:
        con = get_conn()
        row = con.execute(
            "SELECT value FROM app_settings WHERE key = 'lang'"
        ).fetchone()
        con.close()
        if row and row[0] in SUPPORTED_LANGS:
            return row[0]
    except Exception:
        pass
    import os
    env_lang = os.getenv("LANG_UI", "").lower()
    if env_lang in SUPPORTED_LANGS:
        return env_lang
    return DEFAULT_LANG


def load_skill_prompt(skill_name: str, lang: str = None) -> str:
    if skill_name not in SKILL_NAMES:
        raise ValueError(f"Unknown skill: {skill_name}")
    lang = lang or get_lang()
    try:
        con = get_conn()
        row = con.execute(
            "SELECT prompt FROM skill_prompts WHERE skill = ? AND lang = ?",
            [skill_name, lang],
        ).fetchone()
        con.close()
        if row and row[0].strip():
            return row[0]
    except Exception:
        pass
    path = SKILLS_DIR / lang / f"{skill_name}.md"
    if not path.exists():
        path = SKILLS_DIR / DEFAULT_LANG / f"{skill_name}.md"
    return path.read_text(encoding="utf-8")


def load_default_skill_prompt(skill_name: str, lang: str = None) -> str:
    """Always load from file, ignoring DB overrides."""
    if skill_name not in SKILL_NAMES:
        raise ValueError(f"Unknown skill: {skill_name}")
    lang = lang or get_lang()
    path = SKILLS_DIR / lang / f"{skill_name}.md"
    if not path.exists():
        path = SKILLS_DIR / DEFAULT_LANG / f"{skill_name}.md"
    return path.read_text(encoding="utf-8")


def _query_tickers_data(tickers: list[str]) -> list[dict]:
    """Query full data for a given list of tickers (from screener)."""
    if not tickers:
        return []
    con = get_conn()
    placeholders = ",".join(["?"] * len(tickers))
    rows = con.execute(f"""
        SELECT f.ticker, m.company_name, m.sector,
               f.revenue_growth_yoy, f.earnings_growth_yoy, f.gross_margin,
               f.pe_ratio, f.ps_ratio, f.pb_ratio, f.peg_ratio,
               f.analyst_rating, f.analyst_target_price, f.analyst_count,
               f.mspr, f.next_earnings_date, f.last_earnings_date,
               d.close, d.ma_50, d.ma_200, d.rsi_14,
               d.dist_ma20_pct, d.dist_ma50_pct, d.vol_ratio, d.atr_pct,
               d.high_20, d.ma_20, d.pct_chg
        FROM stock_ohlcv_daily d
        JOIN stocks_meta m ON d.ticker = m.ticker
        LEFT JOIN stock_fundamentals f ON d.ticker = f.ticker
        WHERE d.date = (SELECT MAX(date) FROM stock_ohlcv_daily)
          AND d.ticker IN ({placeholders})
        ORDER BY d.vol_ratio DESC NULLS LAST
    """, tickers).fetchall()
    con.close()
    cols = ["ticker","company_name","sector",
            "revenue_growth_yoy","earnings_growth_yoy","gross_margin",
            "pe_ratio","ps_ratio","pb_ratio","peg_ratio",
            "analyst_rating","analyst_target_price","analyst_count",
            "mspr","next_earnings_date","last_earnings_date",
            "close","ma_50","ma_200","rsi_14",
            "dist_ma20_pct","dist_ma50_pct","vol_ratio","atr_pct",
            "high_20","ma_20","pct_chg"]
    return [dict(zip(cols, r)) for r in rows]


def _query_longterm_candidates() -> list[dict]:
    con = get_conn()
    rows = con.execute("""
        SELECT f.ticker, m.company_name, m.sector,
               f.revenue_growth_yoy, f.earnings_growth_yoy, f.gross_margin,
               f.pe_ratio, f.ps_ratio, f.pb_ratio, f.peg_ratio,
               f.analyst_rating, f.analyst_target_price, f.analyst_count,
               f.mspr, f.next_earnings_date, f.last_earnings_date,
               d.close, d.ma_50, d.ma_200, d.rsi_14,
               d.dist_ma20_pct, d.dist_ma50_pct, d.vol_ratio, d.atr_pct,
               d.high_20
        FROM stock_fundamentals f
        JOIN stocks_meta m ON f.ticker = m.ticker
        JOIN stock_ohlcv_daily d ON f.ticker = d.ticker
          AND d.date = (SELECT MAX(date) FROM stock_ohlcv_daily)
        WHERE f.updated_at >= CURRENT_DATE - INTERVAL '7 days'
          AND f.revenue_growth_yoy > 0.10
          AND f.earnings_growth_yoy > 0.10
          AND f.gross_margin > 0.30
          AND f.pe_ratio < 60
          AND d.close > d.ma_200
        ORDER BY f.revenue_growth_yoy DESC
        LIMIT 50
    """).fetchall()
    cols = ["ticker","company_name","sector",
            "revenue_growth_yoy","earnings_growth_yoy","gross_margin",
            "pe_ratio","ps_ratio","pb_ratio","peg_ratio",
            "analyst_rating","analyst_target_price","analyst_count",
            "mspr","next_earnings_date","last_earnings_date",
            "close","ma_50","ma_200","rsi_14",
            "dist_ma20_pct","dist_ma50_pct","vol_ratio","atr_pct","high_20"]
    results = [dict(zip(cols, r)) for r in rows]
    con.close()
    return results


def _query_swing_candidates() -> list[dict]:
    con = get_conn()
    rows = con.execute("""
        SELECT o.ticker, m.company_name, m.sector,
               o.close, o.vol_ratio, o.rsi_14,
               o.dist_ma20_pct, o.dist_ma50_pct,
               o.high_20, o.ma_20, o.ma_50, o.ma_200,
               o.atr_pct, o.pct_chg,
               f.next_earnings_date, f.pe_ratio, f.revenue_growth_yoy
        FROM stock_ohlcv_daily o
        JOIN stocks_meta m ON o.ticker = m.ticker
        LEFT JOIN stock_fundamentals f ON o.ticker = f.ticker
        WHERE o.date = (SELECT MAX(date) FROM stock_ohlcv_daily)
          AND o.close > o.ma_20
          AND o.close > o.ma_50
          AND o.ma_20 > o.ma_50
          AND o.vol_ratio > 1.2
          AND o.rsi_14 BETWEEN 45 AND 75
          AND o.dist_ma20_pct < 0.08
          AND o.atr_pct > 0.01
        ORDER BY o.vol_ratio DESC
        LIMIT 100
    """).fetchall()
    cols = ["ticker","company_name","sector",
            "close","vol_ratio","rsi_14",
            "dist_ma20_pct","dist_ma50_pct",
            "high_20","ma_20","ma_50","ma_200",
            "atr_pct","pct_chg",
            "next_earnings_date","pe_ratio","revenue_growth_yoy"]
    results = [dict(zip(cols, r)) for r in rows]
    con.close()
    return results


def _query_position_data(ticker: str) -> dict:
    con = get_conn()
    ohlcv_row = con.execute("""
        SELECT ticker, close, ma_20, ma_50, ma_200,
               rsi_14, dist_ma20_pct, dist_ma50_pct,
               vol_ratio, atr_pct, pct_chg
        FROM stock_ohlcv_daily
        WHERE ticker = ? AND date = (SELECT MAX(date) FROM stock_ohlcv_daily)
    """, [ticker]).fetchone()

    fund_row = con.execute("""
        SELECT revenue_growth_yoy, earnings_growth_yoy,
               gross_margin, pe_ratio, ps_ratio, peg_ratio,
               analyst_target_price, analyst_rating, analyst_count,
               mspr, next_earnings_date, last_earnings_date, fcf_yield
        FROM stock_fundamentals WHERE ticker = ?
    """, [ticker]).fetchone()

    news_rows = con.execute("""
        SELECT headline, sentiment_label, sentiment_score, published_at
        FROM news WHERE ticker = ?
          AND published_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
        ORDER BY published_at DESC LIMIT 20
    """, [ticker]).fetchall()

    meta_row = con.execute(
        "SELECT company_name, sector, industry FROM stocks_meta WHERE ticker = ?", [ticker]
    ).fetchone()
    con.close()

    result = {"ticker": ticker}
    if ohlcv_row:
        for k, v in zip(["ticker","close","ma_20","ma_50","ma_200","rsi_14",
                          "dist_ma20_pct","dist_ma50_pct","vol_ratio","atr_pct","pct_chg"], ohlcv_row):
            result[k] = v
    if fund_row:
        for k, v in zip(["revenue_growth_yoy","earnings_growth_yoy","gross_margin",
                          "pe_ratio","ps_ratio","peg_ratio","analyst_target_price",
                          "analyst_rating","analyst_count","mspr",
                          "next_earnings_date","last_earnings_date","fcf_yield"], fund_row):
            result[k] = v
    if meta_row:
        result["company_name"] = meta_row[0]
        result["sector"] = meta_row[1]
        result["industry"] = meta_row[2]
    result["news"] = [
        {"headline": r[0], "sentiment_label": r[1], "sentiment_score": r[2], "published_at": str(r[3]) if r[3] else None}
        for r in news_rows
    ]
    return result


def _query_news_for_tickers(tickers: list[str], days: int = 7) -> dict[str, list]:
    if not tickers:
        return {}
    con = get_conn()
    placeholders = ",".join(["?"] * len(tickers))
    rows = con.execute(f"""
        SELECT ticker, headline, sentiment_label, sentiment_score, published_at
        FROM news
        WHERE ticker IN ({placeholders})
          AND published_at >= CURRENT_TIMESTAMP - INTERVAL '{days} days'
        ORDER BY ticker, published_at DESC
    """, tickers).fetchall()
    con.close()

    result: dict[str, list] = {t: [] for t in tickers}
    for r in rows:
        result.setdefault(r[0], []).append({
            "headline": r[1], "sentiment_label": r[2],
            "sentiment_score": r[3], "published_at": str(r[4]) if r[4] else None,
        })
    return result


def gather_context(skill_name: str, params: dict = None) -> str:
    """Query DB and assemble data context for the given skill."""
    params = params or {}

    if skill_name == "longterm_screen":
        custom_tickers = params.get("tickers")
        if custom_tickers:
            candidates = _query_tickers_data(custom_tickers)
            source = f"screener ({len(custom_tickers)} tickers)"
        else:
            candidates = _query_longterm_candidates()
            source = "built-in criteria (growth>10%, margin>30%, PE<60, above MA200)"
        tickers = [c["ticker"] for c in candidates]
        news = _query_news_for_tickers(tickers, days=30)
        for c in candidates:
            c["recent_news"] = news.get(c["ticker"], [])[:5]
        return json.dumps({
            "date": date.today().isoformat(),
            "source": source,
            "candidates_count": len(candidates),
            "candidates": candidates,
        }, default=str, ensure_ascii=False)

    elif skill_name == "swing_screen":
        custom_tickers = params.get("tickers")
        if custom_tickers:
            candidates = _query_tickers_data(custom_tickers)
            source = f"screener ({len(custom_tickers)} tickers)"
        else:
            candidates = _query_swing_candidates()
            source = "built-in criteria (above MA20/50, bull alignment, vol>1.2x, RSI 45-75)"
        tickers = [c["ticker"] for c in candidates]
        news = _query_news_for_tickers(tickers, days=7)
        for c in candidates:
            c["recent_news"] = news.get(c["ticker"], [])[:3]
        return json.dumps({
            "date": date.today().isoformat(),
            "source": source,
            "candidates_count": len(candidates),
            "candidates": candidates,
        }, default=str, ensure_ascii=False)

    elif skill_name == "position_review":
        positions_input = params.get("positions", [])
        if not positions_input:
            return json.dumps({"error": "positions list is required"})

        all_positions = []
        for p in positions_input:
            ticker = (p.get("ticker") or "").upper()
            if not ticker:
                continue
            data = _query_position_data(ticker)
            if p.get("cost_basis") is not None:
                data["cost_basis"] = p["cost_basis"]
            if p.get("shares") is not None:
                data["shares"] = p["shares"]
            all_positions.append(data)

        return json.dumps({
            "date": date.today().isoformat(),
            "positions_count": len(all_positions),
            "positions": all_positions,
        }, default=str, ensure_ascii=False)

    else:
        raise ValueError(f"Unknown skill: {skill_name}")
