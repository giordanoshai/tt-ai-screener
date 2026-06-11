import json
from db.init import get_conn


def analyze_position(ticker: str) -> str:
    """
    Position analysis for a ticker: combines latest technicals, fundamentals, and recent news.
    Gives Claude everything needed to assess the position in one call.
    """
    con = get_conn()
    try:
        ticker = ticker.upper()

        # Latest OHLCV + technicals
        ohlcv = con.execute("""
            SELECT date, close, open, high, low, volume,
                   ma_20, ma_50, ma_200, rsi_14, vol_ratio,
                   dist_ma20_pct, dist_ma50_pct, atr_pct, pct_chg
            FROM stock_ohlcv_daily
            WHERE ticker = ?
            ORDER BY date DESC LIMIT 10
        """, [ticker]).fetchall()

        ohlcv_cols = ["date","close","open","high","low","volume","ma_20","ma_50","ma_200",
                      "rsi_14","vol_ratio","dist_ma20_pct","dist_ma50_pct","atr_pct","pct_chg"]
        ohlcv_data = [dict(zip(ohlcv_cols, r)) for r in ohlcv]

        # Fundamentals
        fund = con.execute("""
            SELECT pe_ratio, ps_ratio, pb_ratio, peg_ratio, market_cap,
                   revenue_growth_yoy, earnings_growth_yoy, gross_margin,
                   roe, fcf_yield, analyst_rating, analyst_target_price,
                   next_earnings_date, updated_at
            FROM stock_fundamentals WHERE ticker = ?
        """, [ticker]).fetchone()

        fund_cols = ["pe_ratio","ps_ratio","pb_ratio","peg_ratio","market_cap",
                     "revenue_growth_yoy","earnings_growth_yoy","gross_margin",
                     "roe","fcf_yield","analyst_rating","analyst_target_price",
                     "next_earnings_date","updated_at"]
        fund_data = dict(zip(fund_cols, fund)) if fund else {}

        # Recent news (last 14 days, max 20 articles)
        news = con.execute("""
            SELECT headline, summary, source, published_at, sentiment_label
            FROM news
            WHERE ticker = ?
              AND published_at >= NOW() - INTERVAL '14 days'
            ORDER BY published_at DESC
            LIMIT 20
        """, [ticker]).fetchall()

        news_cols = ["headline","summary","source","published_at","sentiment_label"]
        news_data = [dict(zip(news_cols, r)) for r in news]

        # Meta
        meta = con.execute("""
            SELECT company_name, sector, industry FROM stocks_meta WHERE ticker = ?
        """, [ticker]).fetchone()

        result = {
            "ticker":       ticker,
            "company_name": meta[0] if meta else "",
            "sector":       meta[1] if meta else "",
            "industry":     meta[2] if meta else "",
            "price_history": ohlcv_data,
            "fundamentals":  fund_data,
            "recent_news":   news_data,
        }

        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        return f"Error: {e}"
    finally:
        con.close()


def get_recent_trades(
    limit: int = 50,
    status: str = None,
    ticker: str = None,
    direction: str = None,
) -> str:
    """
    Query trade records. Useful for reviewing past performance.
    direction: LONG or SHORT
    status: open or closed
    """
    con = get_conn()
    try:
        where = ["1=1"]
        params = []

        if status:
            where.append("status = ?")
            params.append(status.upper())
        if ticker:
            where.append("ticker = ?")
            params.append(ticker.upper())
        if direction:
            d = direction.upper()
            if d in ("L", "LONG", "做多"):
                d = "LONG"
            elif d in ("S", "SHORT", "做空"):
                d = "SHORT"
            where.append("direction = ?")
            params.append(d)

        params.append(limit)

        rows = con.execute(f"""
            SELECT ticker, direction, status, trade_type,
                   entry_price, exit_price, qty, pnl, r_multiple,
                   entry_time, hold_minutes, note
            FROM trades
            WHERE {' AND '.join(where)}
            ORDER BY entry_time DESC
            LIMIT ?
        """, params).fetchall()

        cols = ["ticker","direction","status","trade_type","entry_price","exit_price",
                "qty","pnl","r_multiple","entry_time","hold_minutes","note"]
        result = [dict(zip(cols, r)) for r in rows]
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        return f"Error: {e}"
    finally:
        con.close()
