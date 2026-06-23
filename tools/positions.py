import json
from db.init import get_conn


def list_positions() -> str:
    """
    List all positions with current price, P&L, and key technicals.
    """
    con = get_conn()
    try:
        rows = con.execute("""
            SELECT
                p.ticker,
                m.company_name,
                m.sector,
                p.avg_cost,
                p.shares,
                o.close,
                CASE WHEN p.avg_cost > 0
                     THEN ROUND((o.close - p.avg_cost) / p.avg_cost * 100, 2)
                     ELSE NULL END AS pnl_pct,
                CASE WHEN p.avg_cost > 0
                     THEN ROUND((o.close - p.avg_cost) * p.shares, 2)
                     ELSE NULL END AS pnl_dollar,
                o.rsi_14,
                o.ma_50,
                o.ma_200,
                p.added_at
            FROM user_positions p
            LEFT JOIN stocks_meta m ON p.ticker = m.ticker
            LEFT JOIN stock_ohlcv_daily o ON p.ticker = o.ticker
                AND o.date = (SELECT MAX(date) FROM stock_ohlcv_daily WHERE ticker = p.ticker)
            ORDER BY pnl_dollar DESC NULLS LAST
        """).fetchall()

        cols = ["ticker", "company_name", "sector", "avg_cost", "shares",
                "close", "pnl_pct", "pnl_dollar", "rsi_14", "ma_50", "ma_200", "added_at"]
        result = [dict(zip(cols, r)) for r in rows]

        total_value = sum((r["close"] or 0) * (r["shares"] or 0) for r in result)
        total_cost = sum((r["avg_cost"] or 0) * (r["shares"] or 0) for r in result)
        total_pnl = total_value - total_cost

        return json.dumps({
            "positions": result,
            "summary": {
                "count": len(result),
                "total_value": round(total_value, 2),
                "total_cost": round(total_cost, 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost > 0 else None,
            }
        }, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        con.close()


def add_position(ticker: str, avg_cost: float, shares: int) -> str:
    """
    Add or update a position. If ticker already exists, updates cost and shares.
    ticker: stock symbol
    avg_cost: average cost per share
    shares: number of shares held
    """
    con = get_conn()
    try:
        ticker = ticker.upper().strip()
        if not ticker:
            return json.dumps({"error": "ticker is required"})
        if avg_cost <= 0:
            return json.dumps({"error": "avg_cost must be positive"})
        if shares <= 0:
            return json.dumps({"error": "shares must be positive"})

        meta = con.execute(
            "SELECT ticker FROM stocks_meta WHERE ticker = ?", [ticker]
        ).fetchone()
        if not meta:
            return json.dumps({"error": f"{ticker} not found in database. Run data update first."})

        con.execute("""
            INSERT INTO user_positions (ticker, avg_cost, shares)
            VALUES (?, ?, ?)
            ON CONFLICT (ticker) DO UPDATE SET avg_cost = ?, shares = ?
        """, [ticker, avg_cost, shares, avg_cost, shares])
        return json.dumps({"ok": True, "ticker": ticker, "avg_cost": avg_cost, "shares": shares})
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        con.close()


def remove_position(ticker: str) -> str:
    """Remove a position by ticker."""
    con = get_conn()
    try:
        ticker = ticker.upper().strip()
        deleted = con.execute(
            "DELETE FROM user_positions WHERE ticker = ? RETURNING ticker", [ticker]
        ).fetchone()
        if not deleted:
            return json.dumps({"error": f"{ticker} not in positions"})
        return json.dumps({"ok": True, "removed": ticker})
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        con.close()


def analyze_positions() -> str:
    """
    Comprehensive analysis of all current positions: technicals, fundamentals, news, P&L.
    Requires at least 1 position.
    """
    con = get_conn()
    try:
        positions = con.execute(
            "SELECT ticker, avg_cost, shares FROM user_positions ORDER BY added_at"
        ).fetchall()
        con.close()

        if not positions:
            return json.dumps({"error": "No positions found. Add positions first."})

        from skills import gather_context
        positions_input = [
            {"ticker": r[0], "cost_basis": r[1], "shares": r[2]}
            for r in positions
        ]
        context = gather_context("position_review", {"positions": positions_input})
        return context
    except Exception as e:
        return json.dumps({"error": str(e)})
