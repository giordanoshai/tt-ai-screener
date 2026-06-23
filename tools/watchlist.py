import json
from db.init import get_conn


def list_watchlist() -> str:
    """
    List all tickers in the watchlist with latest price, daily change, and key technicals.
    """
    con = get_conn()
    try:
        rows = con.execute("""
            SELECT
                w.ticker,
                m.company_name,
                m.sector,
                o.close,
                o.pct_chg,
                o.rsi_14,
                o.ma_20,
                o.ma_50,
                o.ma_200,
                o.vol_ratio,
                w.added_at
            FROM user_watchlist w
            LEFT JOIN stocks_meta m ON w.ticker = m.ticker
            LEFT JOIN stock_ohlcv_daily o ON w.ticker = o.ticker
                AND o.date = (SELECT MAX(date) FROM stock_ohlcv_daily WHERE ticker = w.ticker)
            ORDER BY w.added_at DESC
        """).fetchall()

        cols = ["ticker", "company_name", "sector", "close", "pct_chg",
                "rsi_14", "ma_20", "ma_50", "ma_200", "vol_ratio", "added_at"]
        result = [dict(zip(cols, r)) for r in rows]
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        con.close()


def add_to_watchlist(ticker: str) -> str:
    """Add a ticker to the watchlist."""
    con = get_conn()
    try:
        ticker = ticker.upper().strip()
        if not ticker:
            return json.dumps({"error": "ticker is required"})

        meta = con.execute(
            "SELECT ticker FROM stocks_meta WHERE ticker = ?", [ticker]
        ).fetchone()
        if not meta:
            return json.dumps({"error": f"{ticker} not found in database. Run data update first."})

        con.execute(
            "INSERT INTO user_watchlist (ticker) VALUES (?) ON CONFLICT DO NOTHING",
            [ticker],
        )
        return json.dumps({"ok": True, "ticker": ticker})
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        con.close()


def remove_from_watchlist(ticker: str) -> str:
    """Remove a ticker from the watchlist."""
    con = get_conn()
    try:
        ticker = ticker.upper().strip()
        deleted = con.execute(
            "DELETE FROM user_watchlist WHERE ticker = ? RETURNING ticker", [ticker]
        ).fetchone()
        if not deleted:
            return json.dumps({"error": f"{ticker} not in watchlist"})
        return json.dumps({"ok": True, "removed": ticker})
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        con.close()


def analyze_watchlist(analysis_type: str = "longterm") -> str:
    """
    Run longterm or swing analysis on all watchlist tickers.
    analysis_type: 'longterm' | 'swing'
    Requires at least 1 ticker in watchlist.
    """
    con = get_conn()
    try:
        tickers = [r[0] for r in con.execute(
            "SELECT ticker FROM user_watchlist ORDER BY added_at"
        ).fetchall()]
        con.close()

        if not tickers:
            return json.dumps({"error": "Watchlist is empty. Add tickers first."})

        from skills import gather_context
        if analysis_type == "swing":
            context = gather_context("swing_screen", {"tickers": tickers})
        else:
            context = gather_context("longterm_screen", {"tickers": tickers})

        return context
    except Exception as e:
        return json.dumps({"error": str(e)})
